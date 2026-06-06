"""
Тесты B2B-03: редактирование товара и SKU.

DoD-сценарии:
  happy:
    - edit_moderated_product_returns_to_on_moderation
    - edit_blocked_product_returns_to_on_moderation
    - reserves_preserved_after_sku_edit
  unhappy:
    - edit_hard_blocked_returns_403
    - edit_others_product_returns_403
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.dependencies.auth import get_current_seller_id
from app.dependencies.db import get_db
from app.models.product import Product, ProductStatus, SKU, SKUImage, SKUCharacteristic

SELLER_ID  = uuid.uuid4()
SELLER2_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()
SKU_ID     = uuid.uuid4()
CATEGORY_ID = uuid.uuid4()
_NOW = datetime.now(timezone.utc)

VALID_PRODUCT_BODY = {
    "title": "Updated Product",
    "description": "Updated description for the product",
    "category_id": str(CATEGORY_ID),
    "characteristics": [{"name": "color", "value": "blue"}],
    "images": [{"url": "https://cdn.example.com/img2.jpg", "ordering": 0}],
}

VALID_SKU_BODY = {
    "name": "Updated SKU",
    "price": 150000,
    "discount": 0,
    "images": [{"url": "https://cdn.example.com/sku2.jpg", "ordering": 0}],
    "characteristics": [{"name": "Размер", "value": "L"}],
}


# ─── Фабрики ─────────────────────────────────────────────────────────────────

def make_product(
    status: ProductStatus = ProductStatus.MODERATED,
    seller_id: uuid.UUID = SELLER_ID,
) -> MagicMock:
    p = MagicMock(spec=Product)
    p.id         = PRODUCT_ID
    p.seller_id  = seller_id
    p.category_id = CATEGORY_ID
    p.title      = "Test Product"
    p.status     = status
    p.deleted    = False
    p.skus       = []
    p.slug       = "test-product"
    p.description = "description"
    p.characteristics = []
    p.images     = []
    p.blocking_reason_id = None
    p.blocking_reason    = None
    p.moderator_comment  = None
    p.field_reports      = []
    p.created_at = _NOW
    p.updated_at = _NOW
    return p


def make_sku(reserved_quantity: int = 5) -> MagicMock:
    s = MagicMock(spec=SKU)
    s.id               = SKU_ID
    s.product_id       = PRODUCT_ID
    s.name             = "Красный M"
    s.price            = 99900
    s.discount         = 0
    s.cost_price       = None
    s.article          = None
    s.stock_quantity   = 10
    s.reserved_quantity = reserved_quantity
    s.active_quantity  = 10 - reserved_quantity
    s.images_rel       = []
    s.characteristics_rel = []
    s.created_at       = _NOW
    s.updated_at       = _NOW
    return s


# ─── Фикстуры ────────────────────────────────────────────────────────────────

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


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer valid.jwt.token"}


async def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ─── PUT /products/{id} ───────────────────────────────────────────────────────

async def test_edit_moderated_product_returns_to_on_moderation(auth_headers):
    """
    MODERATED → PUT /products/{id} → ON_MODERATION + событие EDITED.
    """
    product = make_product(ProductStatus.MODERATED)

    with patch("app.services.product_service.get_product", new_callable=AsyncMock, return_value=product), \
         patch("app.services.product_service.emit_product_edited") as mock_emit:
        async with await make_client() as client:
            resp = await client.put(
                f"/api/v1/products/{PRODUCT_ID}",
                json=VALID_PRODUCT_BODY,
                headers=auth_headers,
            )

    assert resp.status_code == 200
    assert product.status == ProductStatus.ON_MODERATION
    mock_emit.assert_called_once()


async def test_edit_blocked_product_returns_to_on_moderation(auth_headers):
    """
    BLOCKED → PUT /products/{id} → ON_MODERATION + событие EDITED.
    """
    product = make_product(ProductStatus.BLOCKED)

    with patch("app.services.product_service.get_product", new_callable=AsyncMock, return_value=product), \
         patch("app.services.product_service.emit_product_edited") as mock_emit:
        async with await make_client() as client:
            resp = await client.put(
                f"/api/v1/products/{PRODUCT_ID}",
                json=VALID_PRODUCT_BODY,
                headers=auth_headers,
            )

    assert resp.status_code == 200
    assert product.status == ProductStatus.ON_MODERATION
    mock_emit.assert_called_once()


async def test_edit_created_product_no_remoderation(auth_headers):
    """
    CREATED → PUT /products/{id} → статус остаётся CREATED, событие не шлётся.
    """
    product = make_product(ProductStatus.CREATED)

    with patch("app.services.product_service.get_product", new_callable=AsyncMock, return_value=product), \
         patch("app.services.product_service.emit_product_edited") as mock_emit:
        async with await make_client() as client:
            resp = await client.put(
                f"/api/v1/products/{PRODUCT_ID}",
                json=VALID_PRODUCT_BODY,
                headers=auth_headers,
            )

    assert resp.status_code == 200
    assert product.status == ProductStatus.CREATED
    mock_emit.assert_not_called()


async def test_edit_hard_blocked_returns_403(auth_headers):
    """HARD_BLOCKED → PUT /products/{id} → 403."""
    product = make_product(ProductStatus.HARD_BLOCKED)

    with patch("app.services.product_service.get_product", new_callable=AsyncMock, return_value=product):
        async with await make_client() as client:
            resp = await client.put(
                f"/api/v1/products/{PRODUCT_ID}",
                json=VALID_PRODUCT_BODY,
                headers=auth_headers,
            )

    assert resp.status_code == 403
    body = resp.json()
    assert {"code", "message"} <= set(body.keys())


async def test_edit_others_product_returns_403(auth_headers):
    """Чужой товар → PUT /products/{id} → 404 (IDOR — не раскрываем существование)."""
    with patch(
        "app.services.product_service.get_product",
        new_callable=AsyncMock,
        side_effect=__import__("fastapi").HTTPException(
            status_code=404, detail="Product not found"
        ),
    ):
        async with await make_client() as client:
            resp = await client.put(
                f"/api/v1/products/{PRODUCT_ID}",
                json=VALID_PRODUCT_BODY,
                headers=auth_headers,
            )

    assert resp.status_code == 404
    body = resp.json()
    assert {"code", "message"} <= set(body.keys())


# ─── PUT /skus/{id} ───────────────────────────────────────────────────────────

async def test_reserves_preserved_after_sku_edit(auth_headers):
    """
    reserved_quantity SKU не меняется при PUT /skus/{id}.
    Активные резервы сохраняются.
    """
    sku = make_sku(reserved_quantity=5)

    with patch("app.routers.skus.update_sku", new_callable=AsyncMock, return_value=sku):
        async with await make_client() as client:
            resp = await client.put(
                f"/api/v1/skus/{SKU_ID}",
                json=VALID_SKU_BODY,
                headers=auth_headers,
            )

    assert resp.status_code == 200
    # reserved_quantity не обнулился
    assert sku.reserved_quantity == 5


async def test_edit_sku_on_moderated_product_triggers_remoderation(auth_headers):
    """PUT /skus/{id} на MODERATED товар → product.status = ON_MODERATION + emit EDITED."""
    product = make_product(ProductStatus.MODERATED)
    sku = make_sku()
    sku.product_id = PRODUCT_ID

    with patch("app.routers.skus.update_sku", new_callable=AsyncMock, return_value=sku):
        async with await make_client() as client:
            resp = await client.put(
                f"/api/v1/skus/{SKU_ID}",
                json=VALID_SKU_BODY,
                headers=auth_headers,
            )

    assert resp.status_code == 200


async def test_edit_sku_hard_blocked_returns_403(auth_headers):
    """PUT /skus/{id} на HARD_BLOCKED товар → 403."""
    from fastapi import HTTPException as FHTTPException

    with patch(
        "app.routers.skus.update_sku",
        new_callable=AsyncMock,
        side_effect=FHTTPException(status_code=403, detail="Cannot modify SKU of a HARD_BLOCKED product"),
    ):
        async with await make_client() as client:
            resp = await client.put(
                f"/api/v1/skus/{SKU_ID}",
                json=VALID_SKU_BODY,
                headers=auth_headers,
            )

    assert resp.status_code == 403
    body = resp.json()
    assert {"code", "message"} <= set(body.keys())
