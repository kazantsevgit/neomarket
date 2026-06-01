import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies.auth import get_current_user_id
from app.dependencies.db import get_db
from app.main import app
from app.models.favorite import Favorite
from app.models.product import Product, ProductStatus, SKU
from app.schemas.favorite import PaginatedCatalogProducts

USER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()
CATEGORY_ID = uuid.uuid4()
CATEGORY_NAME = "Electronics"
SKU_ID = uuid.uuid4()


def make_product(*, status=ProductStatus.MODERATED, deleted=False):
    p = MagicMock(spec=Product)
    p.id = PRODUCT_ID
    p.title = "Test Product"
    p.slug = "test-product"
    p.description = "Description"
    p.category_id = CATEGORY_ID
    p.status = status
    p.deleted = deleted
    p.seller_id = uuid.uuid4()
    p.images = [{"id": str(uuid.uuid4()), "url": "/s3/img.jpg", "ordering": 0}]
    p.characteristics = []
    return p


def make_sku(*, price=10000, discount=0, stock=10, reserved=0):
    s = MagicMock(spec=SKU)
    s.id = SKU_ID
    s.product_id = PRODUCT_ID
    s.name = "Default SKU"
    s.price = price
    s.discount = discount
    s.stock_quantity = stock
    s.reserved_quantity = reserved
    s.active_quantity = stock - reserved
    return s


def make_favorite():
    f = MagicMock(spec=Favorite)
    f.user_id = USER_ID
    f.product_id = PRODUCT_ID
    return f


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer valid.jwt.token"}


@pytest.fixture(autouse=True)
def override_auth():
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    yield
    app.dependency_overrides.pop(get_current_user_id, None)


@pytest.fixture
def mock_db():
    fake_db = AsyncMock()
    fake_db.add = MagicMock()
    fake_db.commit = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


async def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_add_to_favorites_returns_201(auth_headers, mock_db):
    product = make_product()
    mock_db.get.side_effect = lambda model, key: (
        product if model == Product and key == PRODUCT_ID else None
    )

    async with await make_client() as client:
        resp = await client.put(
            f"/api/v1/favorites/{PRODUCT_ID}",
            headers=auth_headers,
        )

    assert resp.status_code == 201
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()


async def test_repeat_add_returns_200_not_duplicate(auth_headers, mock_db):
    product = make_product()
    existing_fav = make_favorite()

    async def side_effect_get(model, key):
        if model == Product and key == PRODUCT_ID:
            return product
        if model == Favorite and key == (USER_ID, PRODUCT_ID):
            return existing_fav
        return None

    mock_db.get.side_effect = side_effect_get

    async with await make_client() as client:
        resp = await client.put(
            f"/api/v1/favorites/{PRODUCT_ID}",
            headers=auth_headers,
        )

    assert resp.status_code == 200
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_called()


async def test_delete_nonexistent_returns_204(auth_headers, mock_db):
    mock_db.execute.return_value = AsyncMock()

    async with await make_client() as client:
        resp = await client.delete(
            f"/api/v1/favorites/{PRODUCT_ID}",
            headers=auth_headers,
        )

    assert resp.status_code == 204
    mock_db.execute.assert_awaited_once()
    mock_db.commit.assert_awaited_once()


async def test_blocked_product_excluded_from_list(auth_headers, mock_db):
    product = make_product(status=ProductStatus.BLOCKED)
    product.skus = []

    mock_scalar = MagicMock()
    mock_scalar.scalar.return_value = 0
    mock_scalar.scalars.return_value.all.return_value = []

    mock_db.execute.return_value = mock_scalar

    async with await make_client() as client:
        resp = await client.get("/api/v1/favorites", headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["total_count"] == 0


async def test_user_id_from_query_is_ignored(auth_headers, mock_db):
    mock_get_favorites = AsyncMock()
    mock_get_favorites.return_value = PaginatedCatalogProducts(
        items=[], total_count=0, limit=20, offset=0,
    )

    with patch("app.routers.favorites.get_favorites", mock_get_favorites):
        async with await make_client() as client:
            resp = await client.get(
                f"/api/v1/favorites?user_id={OTHER_USER_ID}",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        mock_get_favorites.assert_called_once()
        _, kwargs = mock_get_favorites.call_args
        assert kwargs["user_id"] == USER_ID
