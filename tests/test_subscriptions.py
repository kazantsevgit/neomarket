import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies.auth import get_current_user_id
from app.dependencies.db import get_db
from app.main import app
from app.models.product import Product, ProductStatus
from app.models.product_subscription import ProductSubscription

USER_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()


def make_product(*, deleted=False):
    p = MagicMock(spec=Product)
    p.id = PRODUCT_ID
    p.title = "Test Product"
    p.slug = "test-product"
    p.description = "Description"
    p.category_id = uuid.uuid4()
    p.status = ProductStatus.MODERATED
    p.deleted = deleted
    p.seller_id = uuid.uuid4()
    p.images = []
    p.characteristics = []
    return p


def make_subscription(*, events=None):
    s = MagicMock(spec=ProductSubscription)
    s.id = uuid.uuid4()
    s.user_id = USER_ID
    s.product_id = PRODUCT_ID
    s.events = events or ["BACK_IN_STOCK", "PRICE_DROP"]
    s.created_at = None
    return s


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

    async def fake_refresh(obj):
        obj.id = uuid.uuid4()
        obj.created_at = datetime.now(timezone.utc)

    fake_db.add = MagicMock()
    fake_db.commit = AsyncMock()
    fake_db.refresh = AsyncMock(side_effect=fake_refresh)
    fake_db.execute = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


async def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_subscribe_returns_201_with_notify_on(auth_headers, mock_db):
    product = make_product()
    mock_db.get.side_effect = lambda model, key: (
        product if model == Product and key == PRODUCT_ID else None
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    async with await make_client() as client:
        resp = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            json={"events": ["BACK_IN_STOCK", "PRICE_DROP"]},
            headers=auth_headers,
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["events"] == ["BACK_IN_STOCK", "PRICE_DROP"]
    assert data["user_id"] == str(USER_ID)
    assert data["product_id"] == str(PRODUCT_ID)
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()


async def test_duplicate_subscription_returns_409(auth_headers, mock_db):
    product = make_product()
    existing_sub = make_subscription()

    async def side_effect_get(model, key):
        if model == Product and key == PRODUCT_ID:
            return product
        return None

    mock_db.get.side_effect = side_effect_get

    mock_scalar = MagicMock()
    mock_scalar.scalar_one_or_none.return_value = existing_sub
    mock_db.execute.return_value = mock_scalar

    async with await make_client() as client:
        resp = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            json={"events": ["BACK_IN_STOCK"]},
            headers=auth_headers,
        )

    assert resp.status_code == 409
    data = resp.json()
    assert data["code"] == "SUBSCRIPTION_ALREADY_EXISTS"
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_called()


async def test_invalid_notify_on_returns_400(auth_headers, mock_db):
    async with await make_client() as client:
        resp = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            json={"events": ["INVALID_EVENT"]},
            headers=auth_headers,
        )

    assert resp.status_code == 422


async def test_empty_notify_on_returns_400(auth_headers, mock_db):
    async with await make_client() as client:
        resp = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            json={"events": []},
            headers=auth_headers,
        )

    assert resp.status_code == 422


async def test_subscribe_to_unknown_product_returns_404(auth_headers, mock_db):
    mock_db.get.return_value = None

    async with await make_client() as client:
        resp = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            json={"events": ["BACK_IN_STOCK"]},
            headers=auth_headers,
        )

    assert resp.status_code == 404
    data = resp.json()
    assert data["code"] == "PRODUCT_NOT_FOUND"
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_called()


async def test_user_id_from_query_is_ignored(auth_headers, mock_db):
    product = make_product()
    mock_db.get.side_effect = lambda model, key: (
        product if model == Product and key == PRODUCT_ID else None
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    other_user = uuid.uuid4()
    async with await make_client() as client:
        resp = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe?user_id={other_user}",
            json={"events": ["BACK_IN_STOCK"]},
            headers=auth_headers,
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["user_id"] == str(USER_ID)
    assert data["user_id"] != str(other_user)


async def test_unsubscribe_returns_204(auth_headers, mock_db):
    mock_db.execute.return_value = AsyncMock()

    async with await make_client() as client:
        resp = await client.delete(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            headers=auth_headers,
        )

    assert resp.status_code == 204
    mock_db.execute.assert_awaited_once()
    mock_db.commit.assert_awaited_once()
