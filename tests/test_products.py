import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies.auth import get_current_seller_id
from app.dependencies.db import get_db
from app.main import app

SELLER_ID = uuid.uuid4()
CATEGORY_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()

VALID_BODY = {
    "title": "Test Product",
    "description": "Some description",
    "category_id": str(CATEGORY_ID),
    "characteristics": [{"name": "color", "value": "red"}],
    "images": [{"url": "https://cdn.example.com/img1.jpg", "ordering": 0}],
}


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer valid.jwt.token"}


@pytest.fixture(autouse=True)
def mock_seller_jwt():
    app.dependency_overrides[get_current_seller_id] = lambda: SELLER_ID
    yield
    app.dependency_overrides.pop(get_current_seller_id, None)


@pytest.fixture(autouse=True)
def mock_db():
    fake_db = AsyncMock()
    fake_db.add = MagicMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


def _category_exists(mock_db):
    category = MagicMock()
    category.id = CATEGORY_ID
    result = MagicMock()
    result.scalar_one_or_none.return_value = category
    mock_db.execute = AsyncMock(return_value=result)


def _category_missing(mock_db):
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result)


async def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_create_product_returns_201_with_created_status(auth_headers, mock_db):
    _category_exists(mock_db)

    async def fake_refresh(product):
        if product.id is None:
            product.id = PRODUCT_ID

    mock_db.refresh = AsyncMock(side_effect=fake_refresh)

    async with await make_client() as client:
        resp = await client.post("/api/v1/products", json=VALID_BODY, headers=auth_headers)

    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "CREATED"
    assert data["skus"] == []
    assert data["slug"] == "test-product"
    assert data["deleted"] is False
    assert len(data["images"]) == 1
    assert data["images"][0]["url"] == VALID_BODY["images"][0]["url"]
    assert data["images"][0]["ordering"] == 0
    assert "id" in data["images"][0]
    assert len(data["characteristics"]) == 1
    assert data["characteristics"][0]["name"] == "color"
    assert data["characteristics"][0]["value"] == "red"
    assert "created_at" in data
    assert "updated_at" in data
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()


async def test_seller_id_taken_from_jwt(auth_headers, mock_db):
    _category_exists(mock_db)

    async def fake_refresh(product):
        if product.id is None:
            product.id = PRODUCT_ID

    mock_db.refresh = AsyncMock(side_effect=fake_refresh)

    body_with_fake_seller = {**VALID_BODY, "seller_id": str(uuid.uuid4())}
    async with await make_client() as client:
        resp = await client.post("/api/v1/products", json=body_with_fake_seller, headers=auth_headers)

    assert resp.status_code == 201
    assert resp.json()["seller_id"] == str(SELLER_ID)
    added_product = mock_db.add.call_args[0][0]
    assert added_product.seller_id == SELLER_ID


async def test_missing_images_returns_400(auth_headers):
    body = {k: v for k, v in VALID_BODY.items() if k != "images"}
    async with await make_client() as client:
        resp = await client.post("/api/v1/products", json=body, headers=auth_headers)

    assert resp.status_code == 422
    body = resp.json()
    assert {"code", "message"} <= set(body.keys())
    assert body["code"] == "VALIDATION_ERROR"


async def test_missing_category_returns_400(auth_headers):
    body = {k: v for k, v in VALID_BODY.items() if k != "category_id"}
    async with await make_client() as client:
        resp = await client.post("/api/v1/products", json=body, headers=auth_headers)

    assert resp.status_code == 422
    body = resp.json()
    assert {"code", "message"} <= set(body.keys())
    assert body["code"] == "VALIDATION_ERROR"


async def test_invalid_category_id_returns_400(auth_headers):
    body = {**VALID_BODY, "category_id": "not-a-uuid"}
    async with await make_client() as client:
        resp = await client.post("/api/v1/products", json=body, headers=auth_headers)

    assert resp.status_code == 422
    body = resp.json()
    assert {"code", "message"} <= set(body.keys())
    assert body["code"] == "VALIDATION_ERROR"


async def test_nonexistent_category_id_returns_400(auth_headers, mock_db):
    _category_missing(mock_db)
    nonexistent_id = uuid.uuid4()
    body = {**VALID_BODY, "category_id": str(nonexistent_id)}

    async with await make_client() as client:
        resp = await client.post("/api/v1/products", json=body, headers=auth_headers)

    assert resp.status_code == 400
    assert resp.status_code in (400, 404)
    body = resp.json()
    assert {"code", "message"} <= set(body.keys())
