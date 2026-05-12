import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch, MagicMock

from app.main import app
from app.models.product import ProductStatus
from app.dependencies.auth import get_current_seller_id
from app.dependencies.db import get_db

SELLER_ID = uuid.uuid4()
CATEGORY_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()

VALID_BODY = {
    "title": "Test Product",
    "description": "Some description",
    "category_id": str(CATEGORY_ID),
    "attributes": {"color": "red"},
    "images": ["https://cdn.example.com/img1.jpg"],
}


def mock_product():
    m = MagicMock()
    m.__dict__ = {
        "id": PRODUCT_ID,
        "seller_id": SELLER_ID,
        "title": VALID_BODY["title"],
        "description": VALID_BODY["description"],
        "category_id": CATEGORY_ID,
        "attributes": VALID_BODY["attributes"],
        "images": VALID_BODY["images"],
        "status": ProductStatus.CREATED,
    }
    return m


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer valid.jwt.token"}


@pytest.fixture(autouse=True)
def mock_seller_jwt():
    # Use FastAPI's dependency_overrides so Depends() picks up the mock,
    # instead of patching the name (which doesn't affect already-bound Depends).
    app.dependency_overrides[get_current_seller_id] = lambda: SELLER_ID
    yield
    app.dependency_overrides.pop(get_current_seller_id, None)


@pytest.fixture(autouse=True)
def mock_db():
    fake_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield
    app.dependency_overrides.pop(get_db, None)


async def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_create_product_returns_201_with_created_status(auth_headers):
    with patch("app.routers.products.create_product", return_value=mock_product()):
        async with await make_client() as client:
            resp = await client.post("/api/v1/products", json=VALID_BODY, headers=auth_headers)

    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "CREATED"
    assert data["skus"] == []


async def test_seller_id_taken_from_jwt(auth_headers):
    body_with_fake_seller = {**VALID_BODY, "seller_id": str(uuid.uuid4())}

    with patch("app.routers.products.create_product", return_value=mock_product()) as mock_svc:
        async with await make_client() as client:
            resp = await client.post("/api/v1/products", json=body_with_fake_seller, headers=auth_headers)

    assert resp.status_code == 201
    call_kwargs = mock_svc.call_args.kwargs
    assert call_kwargs["seller_id"] == SELLER_ID
    assert resp.json()["seller_id"] == str(SELLER_ID)


async def test_missing_images_returns_400(auth_headers):
    body = {k: v for k, v in VALID_BODY.items() if k != "images"}
    async with await make_client() as client:
        resp = await client.post("/api/v1/products", json=body, headers=auth_headers)

    assert resp.status_code == 422
    fields = [e["loc"][-1] for e in resp.json()["detail"]]
    assert "images" in fields


async def test_missing_category_returns_400(auth_headers):
    body = {k: v for k, v in VALID_BODY.items() if k != "category_id"}
    async with await make_client() as client:
        resp = await client.post("/api/v1/products", json=body, headers=auth_headers)

    assert resp.status_code == 422
    fields = [e["loc"][-1] for e in resp.json()["detail"]]
    assert "category_id" in fields


async def test_invalid_category_id_returns_400(auth_headers):
    body = {**VALID_BODY, "category_id": "not-a-uuid"}
    async with await make_client() as client:
        resp = await client.post("/api/v1/products", json=body, headers=auth_headers)

    assert resp.status_code == 422
    fields = [e["loc"][-1] for e in resp.json()["detail"]]
    assert "category_id" in fields