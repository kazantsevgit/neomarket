"""
B2B-7: GET /api/v1/products — каталог для B2C (X-Service-Key).
"""
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.dependencies.db import get_db
from app.main import app
from app.models.product import Product, ProductStatus, SKU
from app.services.catalog_service import is_catalog_visible

SELLER_ID = uuid.uuid4()
CATEGORY_ID = uuid.uuid4()
VISIBLE_ID = uuid.uuid4()
HARD_BLOCKED_ID = uuid.uuid4()
OUT_OF_STOCK_ID = uuid.uuid4()
DELETED_ID = uuid.uuid4()
HIDDEN_BATCH_ID = uuid.uuid4()
_NOW = datetime.now(timezone.utc)

SERVICE_HEADERS = {"X-Service-Key": settings.B2B_SERVICE_KEY}


def _make_sku(*, stock: int = 10, reserved: int = 0) -> MagicMock:
    sku = MagicMock(spec=SKU)
    sku.id = uuid.uuid4()
    sku.product_id = VISIBLE_ID
    sku.name = "256GB Black"
    sku.price = 12_999_000
    sku.discount = 0
    sku.cost_price = 9_500_000
    sku.stock_quantity = stock
    sku.reserved_quantity = reserved
    sku.article = None
    sku.images_rel = []
    sku.characteristics_rel = []
    sku.created_at = _NOW
    sku.updated_at = _NOW
    return sku


def _make_product(
    product_id: uuid.UUID,
    *,
    status: ProductStatus = ProductStatus.MODERATED,
    deleted: bool = False,
    skus: list | None = None,
) -> MagicMock:
    p = MagicMock(spec=Product)
    p.id = product_id
    p.seller_id = SELLER_ID
    p.title = "iPhone 15 Pro Max"
    p.slug = "iphone-15-pro-max"
    p.description = "Flagship"
    p.category_id = CATEGORY_ID
    p.status = status
    p.deleted = deleted
    p.images = [{"id": str(uuid.uuid4()), "url": "/s3/front.jpg", "ordering": 0}]
    p.characteristics = [{"id": str(uuid.uuid4()), "name": "Бренд", "value": "Apple"}]
    p.skus = skus if skus is not None else [_make_sku()]
    p.blocking_reason_id = None
    p.blocking_reason = None
    p.moderator_comment = None
    p.field_reports = []
    p.created_at = _NOW
    p.updated_at = _NOW
    return p


VISIBLE_PRODUCT = _make_product(VISIBLE_ID)
HARD_BLOCKED_PRODUCT = _make_product(HARD_BLOCKED_ID, status=ProductStatus.HARD_BLOCKED)
OUT_OF_STOCK_PRODUCT = _make_product(
    OUT_OF_STOCK_ID,
    skus=[_make_sku(stock=2, reserved=2)],
)
DELETED_PRODUCT = _make_product(DELETED_ID, deleted=True)
HIDDEN_BATCH_PRODUCT = _make_product(HIDDEN_BATCH_ID, status=ProductStatus.BLOCKED)

ALL_PRODUCTS = [
    VISIBLE_PRODUCT,
    HARD_BLOCKED_PRODUCT,
    OUT_OF_STOCK_PRODUCT,
    DELETED_PRODUCT,
    HIDDEN_BATCH_PRODUCT,
]


class CatalogFakeDB:
    """Имитация БД: фильтрация по тем же правилам, что is_catalog_visible."""

    def __init__(self, products: list, *, batch_ids: list[uuid.UUID] | None = None):
        self._products = products
        self._batch_ids = batch_ids

    def _visible(self) -> list:
        items = [p for p in self._products if is_catalog_visible(p)]
        if self._batch_ids is not None:
            allowed = set(self._batch_ids)
            items = [p for p in items if p.id in allowed]
        return items

    async def execute(self, stmt):
        visible = self._visible()
        result = MagicMock()
        if "count(" in str(stmt).lower():
            result.scalar_one.return_value = len(visible)
        else:
            scalars = MagicMock()
            scalars.unique.return_value.all.return_value = visible
            result.scalars.return_value = scalars
        return result


@pytest.fixture
def catalog_db():
    fake_db = CatalogFakeDB(ALL_PRODUCTS)
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


async def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _response_json_without_sensitive_fields(data: object) -> str:
    return json.dumps(data)


async def test_catalog_returns_moderated_in_stock_products(catalog_db):
    async with await make_client() as client:
        resp = await client.get("/api/v1/products", headers=SERVICE_HEADERS)

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["id"] == str(VISIBLE_ID)
    assert item["status"] == "MODERATED"
    assert len(item["skus"]) >= 1
    assert item["skus"][0]["active_quantity"] > 0


async def test_catalog_excludes_hard_blocked(catalog_db):
    async with await make_client() as client:
        resp = await client.get("/api/v1/products", headers=SERVICE_HEADERS)

    ids = {item["id"] for item in resp.json()["items"]}
    assert str(HARD_BLOCKED_ID) not in ids
    assert str(OUT_OF_STOCK_ID) not in ids
    assert str(DELETED_ID) not in ids
    assert str(HIDDEN_BATCH_ID) not in ids


def test_is_catalog_visible_rules():
    assert is_catalog_visible(VISIBLE_PRODUCT) is True
    assert is_catalog_visible(HARD_BLOCKED_PRODUCT) is False
    assert is_catalog_visible(OUT_OF_STOCK_PRODUCT) is False
    assert is_catalog_visible(DELETED_PRODUCT) is False
    assert is_catalog_visible(HIDDEN_BATCH_PRODUCT) is False


async def test_catalog_missing_service_key_returns_401():
    async with await make_client() as client:
        resp = await client.get("/api/v1/products")

    assert resp.status_code == 401


async def test_catalog_missing_service_key_invalid_key_returns_401():
    async with await make_client() as client:
        resp = await client.get(
            "/api/v1/products",
            headers={"X-Service-Key": "wrong-key"},
        )

    assert resp.status_code == 401


async def test_catalog_response_has_no_cost_price(catalog_db):
    async with await make_client() as client:
        resp = await client.get("/api/v1/products", headers=SERVICE_HEADERS)

    body = _response_json_without_sensitive_fields(resp.json())
    assert "cost_price" not in body
    assert "reserved_quantity" not in body


async def test_batch_ids_returns_visible_subset():
    batch_ids = [VISIBLE_ID, HARD_BLOCKED_ID, HIDDEN_BATCH_ID]
    fake_db = CatalogFakeDB(ALL_PRODUCTS, batch_ids=batch_ids)
    app.dependency_overrides[get_db] = lambda: fake_db

    try:
        ids_param = ",".join(str(i) for i in batch_ids)
        async with await make_client() as client:
            resp = await client.get(
                f"/api/v1/products?ids={ids_param}",
                headers=SERVICE_HEADERS,
            )

        assert resp.status_code == 200
        returned_ids = {item["id"] for item in resp.json()["items"]}
        assert returned_ids == {str(VISIBLE_ID)}
        assert str(HARD_BLOCKED_ID) not in returned_ids
        assert str(HIDDEN_BATCH_ID) not in returned_ids
    finally:
        app.dependency_overrides.pop(get_db, None)
