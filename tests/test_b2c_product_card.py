"""
B2C-3: GET /api/v1/products/{id} — карточка товара для покупателя.
Покупатель смотрит фото, читает описание, выбирает вариант.
cost_price / reserved_quantity не должны просочиться в ответ.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies.db import get_db
from app.main import app
from app.models.product import Product, ProductStatus, SKU

PRODUCT_ID = uuid.uuid4()
SKU_ID = uuid.uuid4()
CATEGORY_ID = uuid.uuid4()
_NOW = datetime.now(timezone.utc)
SERVICE_KEY = "dev-b2b-service-key"


def make_product(
    *,
    status: ProductStatus = ProductStatus.MODERATED,
    deleted: bool = False,
) -> MagicMock:
    p = MagicMock(spec=Product)
    p.id = PRODUCT_ID
    p.seller_id = uuid.uuid4()
    p.title = "iPhone 15 Pro Max"
    p.slug = "iphone-15-pro-max"
    p.description = "Флагманский смартфон Apple"
    p.category_id = CATEGORY_ID
    p.status = status
    p.deleted = deleted
    p.images = [
        {"url": "https://cdn.neomarket.ru/images/iphone15-front.jpg", "ordering": 0},
    ]
    p.characteristics = [
        {"name": "Бренд", "value": "Apple"},
    ]
    p.skus = []
    p.created_at = _NOW
    p.updated_at = _NOW
    return p


def make_sku(*, stock_quantity: int = 5, reserved_quantity: int = 0) -> MagicMock:
    s = MagicMock(spec=SKU)
    s.id = SKU_ID
    s.product_id = PRODUCT_ID
    s.name = "256GB Black"
    s.price = 12_999_000
    s.discount = 0
    s.stock_quantity = stock_quantity
    s.reserved_quantity = reserved_quantity
    s.active_quantity = max(0, stock_quantity - reserved_quantity)

    img = MagicMock()
    img.url = "/s3/iphone15-black-256.jpg"
    img.ordering = 0
    s.images_rel = [img]

    ch = MagicMock()
    ch.name = "Цвет"
    ch.value = "Чёрный"
    s.characteristics_rel = [ch]
    s.created_at = _NOW
    s.updated_at = _NOW
    return s


@pytest.fixture(autouse=True)
def mock_db():
    fake_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


async def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── Happy path ────────────────────────────────────────────────────────────────

async def test_product_card_returns_full_data_with_skus(mock_db):
    product = make_product()
    product.skus = [make_sku()]
    mock_db.get.return_value = product

    async with await make_client() as client:
        resp = await client.get(
            f"/api/v1/products/{PRODUCT_ID}",
            headers={"X-Service-Key": SERVICE_KEY},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(PRODUCT_ID)
    assert data["slug"] == "iphone-15-pro-max"
    assert data["title"] == "iPhone 15 Pro Max"
    assert data["description"] == "Флагманский смартфон Apple"
    assert data["status"] == "MODERATED"

    assert len(data["images"]) == 1
    assert data["images"][0]["url"] == "https://cdn.neomarket.ru/images/iphone15-front.jpg"
    assert data["images"][0]["ordering"] == 0

    assert len(data["characteristics"]) == 1
    assert data["characteristics"][0] == {"name": "Бренд", "value": "Apple"}

    assert len(data["skus"]) == 1
    sku = data["skus"][0]
    assert sku["id"] == str(SKU_ID)
    assert sku["name"] == "256GB Black"
    assert sku["price"] == 12_999_000
    assert sku["discount"] == 0
    assert sku["image"] == "/s3/iphone15-black-256.jpg"
    assert sku["active_quantity"] == 5
    assert sku["in_stock"] is True
    assert len(sku["characteristics"]) == 1

    for forbidden in ("seller_id", "category_id", "created_at", "updated_at", "deleted",
                      "blocking_reason", "field_reports", "moderator_comment"):
        assert forbidden not in data, f"{forbidden} should not leak in B2C response"


# ── Security: seller-internal fields ─────────────────────────────────────────

async def test_cost_price_absent_in_response(mock_db):
    product = make_product()
    product.skus = [make_sku()]
    mock_db.get.return_value = product

    async with await make_client() as client:
        resp = await client.get(
            f"/api/v1/products/{PRODUCT_ID}",
            headers={"X-Service-Key": SERVICE_KEY},
        )

    assert resp.status_code == 200
    assert 'cost_price' not in resp.json()['skus'][0]


async def test_reserved_quantity_absent_in_response(mock_db):
    product = make_product()
    product.skus = [make_sku()]
    mock_db.get.return_value = product

    async with await make_client() as client:
        resp = await client.get(
            f"/api/v1/products/{PRODUCT_ID}",
            headers={"X-Service-Key": SERVICE_KEY},
        )

    assert resp.status_code == 200
    assert 'reserved_quantity' not in resp.json()['skus'][0]


# ── Edge cases ───────────────────────────────────────────────────────────────

async def test_blocked_product_returns_404(mock_db):
    product = make_product(status=ProductStatus.BLOCKED)
    mock_db.get.return_value = product

    async with await make_client() as client:
        resp = await client.get(
            f"/api/v1/products/{PRODUCT_ID}",
            headers={"X-Service-Key": SERVICE_KEY},
        )

    assert resp.status_code == 404


async def test_deleted_product_returns_404(mock_db):
    product = make_product(deleted=True)
    mock_db.get.return_value = product

    async with await make_client() as client:
        resp = await client.get(
            f"/api/v1/products/{PRODUCT_ID}",
            headers={"X-Service-Key": SERVICE_KEY},
        )

    assert resp.status_code == 404


async def test_nonexistent_product_returns_404(mock_db):
    mock_db.get.return_value = None

    async with await make_client() as client:
        resp = await client.get(
            f"/api/v1/products/{PRODUCT_ID}",
            headers={"X-Service-Key": SERVICE_KEY},
        )

    assert resp.status_code == 404


async def test_sku_without_stock_is_shown_as_unavailable(mock_db):
    product = make_product()
    product.skus = [make_sku(stock_quantity=0, reserved_quantity=0)]
    mock_db.get.return_value = product

    async with await make_client() as client:
        resp = await client.get(
            f"/api/v1/products/{PRODUCT_ID}",
            headers={"X-Service-Key": SERVICE_KEY},
        )

    assert resp.status_code == 200
    sku = resp.json()["skus"][0]
    assert sku["in_stock"] is False
    assert sku["active_quantity"] == 0
