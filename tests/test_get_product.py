"""
GET /api/v1/products/{id} — канон-flow «просмотр карточки» (B2B-5).
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies.auth import get_optional_current_seller_id
from app.dependencies.db import get_db
from app.main import app
from app.models.product import Product, ProductStatus, SKU

SELLER_ID = uuid.uuid4()
OTHER_SELLER_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()
CATEGORY_ID = uuid.uuid4()
SKU_ID = uuid.uuid4()
BLOCKING_REASON_ID = uuid.uuid4()
_NOW = datetime.now(timezone.utc)


def make_product(
    *,
    status: ProductStatus = ProductStatus.MODERATED,
    seller_id: uuid.UUID = SELLER_ID,
    blocking_reason: dict | None = None,
    field_reports: list | None = None,
) -> MagicMock:
    p = MagicMock(spec=Product)
    p.id = PRODUCT_ID
    p.seller_id = seller_id
    p.title = "iPhone 15 Pro Max"
    p.slug = "iphone-15-pro-max"
    p.description = "Flagship smartphone"
    p.category_id = CATEGORY_ID
    p.status = status
    p.deleted = False
    p.blocking_reason_id = blocking_reason["id"] if blocking_reason else None
    p.blocking_reason = blocking_reason
    p.moderator_comment = blocking_reason.get("comment") if blocking_reason else None
    p.field_reports = field_reports if field_reports is not None else []
    p.images = [{"id": str(uuid.uuid4()), "url": "/s3/front.jpg", "ordering": 0}]
    p.characteristics = [
        {"id": str(uuid.uuid4()), "name": "Бренд", "value": "Apple"},
    ]
    p.skus = []
    p.created_at = _NOW
    p.updated_at = _NOW
    return p


def make_sku(*, cost_price: int = 9_500_000) -> MagicMock:
    s = MagicMock(spec=SKU)
    s.id = SKU_ID
    s.product_id = PRODUCT_ID
    s.name = "256GB Black"
    s.price = 12_999_000
    s.discount = 0
    s.cost_price = cost_price
    s.article = None
    s.stock_quantity = 12
    s.reserved_quantity = 2
    s.images_rel = []
    s.characteristics_rel = []
    s.created_at = _NOW
    s.updated_at = _NOW
    return s


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer valid.jwt.token"}


@pytest.fixture(autouse=True)
def override_seller():
    app.dependency_overrides[get_optional_current_seller_id] = lambda: SELLER_ID
    yield
    app.dependency_overrides.pop(get_optional_current_seller_id, None)


@pytest.fixture
def mock_db():
    fake_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


async def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_get_moderated_product_returns_full_payload(auth_headers, mock_db):
    product = make_product(status=ProductStatus.MODERATED)
    product.skus = [make_sku(cost_price=9_500_000)]
    mock_db.get.return_value = product

    async with await make_client() as client:
        resp = await client.get(f"/api/v1/products/{PRODUCT_ID}", headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "MODERATED"
    assert data["title"] == product.title
    assert data["blocking_reason"] is None
    assert data["field_reports"] == []
    assert len(data["skus"]) == 1
    assert data["skus"][0]["cost_price"] == 9_500_000
    assert data["skus"][0]["reserved_quantity"] == 2
    mock_db.get.assert_awaited_once()


async def test_get_blocked_product_returns_blocking_reason_and_field_reports(
    auth_headers, mock_db
):
    blocking_reason = {
        "id": str(BLOCKING_REASON_ID),
        "title": "Описание не соответствует товару",
        "comment": "Несоответствие описания и фотографий",
    }
    field_reports = [
        {
            "field_name": "description",
            "sku_id": None,
            "comment": "В описании указан другой материал",
        },
        {
            "field_name": "sku_image",
            "sku_id": str(SKU_ID),
            "comment": "Фото SKU не соответствует цвету",
        },
    ]
    product = make_product(
        status=ProductStatus.BLOCKED,
        blocking_reason=blocking_reason,
        field_reports=field_reports,
    )
    product.skus = [make_sku()]
    mock_db.get.return_value = product

    async with await make_client() as client:
        resp = await client.get(f"/api/v1/products/{PRODUCT_ID}", headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "BLOCKED"
    assert data["blocking_reason"]["title"] == "Описание не соответствует товару"
    assert len(data["field_reports"]) == 2
    assert data["field_reports"][0]["field_name"] == "description"
    assert data["field_reports"][1]["sku_id"] == str(SKU_ID)


async def test_get_others_product_returns_404(auth_headers, mock_db):
    product = make_product(seller_id=OTHER_SELLER_ID)
    mock_db.get.return_value = product

    async with await make_client() as client:
        resp = await client.get(f"/api/v1/products/{PRODUCT_ID}", headers=auth_headers)

    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND" or "not found" in resp.json()["message"].lower()


async def test_get_nonexistent_returns_404(auth_headers, mock_db):
    mock_db.get.return_value = None

    async with await make_client() as client:
        resp = await client.get(f"/api/v1/products/{PRODUCT_ID}", headers=auth_headers)

    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND" or "not found" in resp.json()["message"].lower()