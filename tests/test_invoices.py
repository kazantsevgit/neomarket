"""
Тесты endpoint POST /api/v1/invoices.

Сценарии (canonic flow «создание накладной»):
  happy:
    create_invoice_with_moderated_sku_returns_201
  unhappy:
    empty_items_returns_400
    non_moderated_sku_returns_400
    others_sku_returns_403
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies.auth import get_current_seller_id
from app.dependencies.db import get_db
from app.main import app
from app.models.product import Product, ProductStatus, SKU

SELLER_ID = uuid.uuid4()
OTHER_SELLER_ID = uuid.uuid4()
SKU_ID = uuid.uuid4()
OTHER_SKU_ID = uuid.uuid4()
INVOICE_ID = uuid.uuid4()
INVOICE_ITEM_ID = uuid.uuid4()
_NOW = "2026-03-17T10:00:00.000Z"

VALID_BODY = {
    "items": [
        {"sku_id": str(SKU_ID), "quantity": 10},
    ],
}


def make_sku(
    sku_id: uuid.UUID = SKU_ID,
    seller_id: uuid.UUID = SELLER_ID,
    status: ProductStatus = ProductStatus.MODERATED,
    name: str = "256GB Black",
) -> MagicMock:
    sku = MagicMock(spec=SKU)
    sku.id = sku_id
    sku.name = name
    product = MagicMock(spec=Product)
    product.seller_id = seller_id
    product.status = status
    sku.product = product
    return sku


def make_invoice(sku_id: uuid.UUID = SKU_ID, sku_name: str = "256GB Black") -> MagicMock:
    invoice = MagicMock()
    invoice.id = INVOICE_ID
    invoice.status = "PENDING"
    invoice.created_at = _NOW
    item = MagicMock()
    item.id = INVOICE_ITEM_ID
    item.sku_id = sku_id
    item.sku_name = sku_name
    item.quantity = 10
    item.accepted_quantity = None
    invoice.items = [item]
    return invoice


def _db_with_skus(skus: list[MagicMock]) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = skus
    db.execute.return_value = result
    db.refresh = AsyncMock()
    return db


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer valid.jwt.token"}


@pytest.fixture(autouse=True)
def override_auth():
    app.dependency_overrides[get_current_seller_id] = lambda: SELLER_ID
    yield
    app.dependency_overrides.pop(get_current_seller_id, None)


@pytest.fixture(autouse=True)
def override_db():
    fake_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


async def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_create_invoice_with_moderated_sku_returns_201(auth_headers):
    """Happy path: создание накладной с валидным MODERATED SKU → 201 + статус PENDING."""
    import datetime

    sku = make_sku()

    real_invoice = None

    def fake_add(obj):
        nonlocal real_invoice
        real_invoice = obj

    async def fake_refresh(obj):
        if obj.id is None:
            obj.id = INVOICE_ID
        if obj.created_at is None:
            obj.created_at = datetime.datetime.now(datetime.timezone.utc)
        if not obj.items:
            item = MagicMock()
            item.id = INVOICE_ITEM_ID
            item.sku_id = SKU_ID
            item.sku_name = "256GB Black"
            item.quantity = 10
            item.accepted_quantity = None
            obj.items = [item]

    db = _db_with_skus([sku])
    db.add = MagicMock(side_effect=fake_add)
    db.refresh = AsyncMock(side_effect=fake_refresh)
    db.commit = AsyncMock()
    db.flush = AsyncMock()

    app.dependency_overrides[get_db] = lambda: db

    async with await make_client() as client:
        resp = await client.post("/api/v1/invoices", json=VALID_BODY, headers=auth_headers)

    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "PENDING"
    assert len(data["items"]) == 1
    assert data["items"][0]["sku_id"] == str(SKU_ID)
    assert data["items"][0]["sku_name"] == "256GB Black"
    assert data["items"][0]["quantity"] == 10
    assert data["items"][0]["accepted_quantity"] is None
    assert "id" in data
    assert "created_at" in data


async def test_empty_items_returns_400(auth_headers):
    """Пустой список items → 400."""
    async with await make_client() as client:
        resp = await client.post("/api/v1/invoices", json={"items": []}, headers=auth_headers)

    assert resp.status_code == 400
    data = resp.json()
    assert data["code"] == "INVALID_REQUEST"
    assert "At least one item is required" in data["message"]


async def test_non_moderated_sku_returns_400(auth_headers):
    """SKU не-MODERATED товара → 400."""
    sku = make_sku(status=ProductStatus.CREATED)
    db = _db_with_skus([sku])
    app.dependency_overrides[get_db] = lambda: db

    async with await make_client() as client:
        resp = await client.post("/api/v1/invoices", json=VALID_BODY, headers=auth_headers)

    assert resp.status_code == 400
    data = resp.json()
    assert data["code"] == "INVALID_REQUEST"
    assert "MODERATED" in data["message"]


async def test_others_sku_returns_403(auth_headers):
    """SKU чужого продавца → 403."""
    sku = make_sku(seller_id=OTHER_SELLER_ID)
    db = _db_with_skus([sku])
    app.dependency_overrides[get_db] = lambda: db

    async with await make_client() as client:
        resp = await client.post("/api/v1/invoices", json=VALID_BODY, headers=auth_headers)

    assert resp.status_code == 403
    data = resp.json()
    assert data["code"] == "NOT_OWNER"
    assert "do not belong" in data["message"]
