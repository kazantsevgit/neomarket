"""
Тесты US-ORD-01: оформление заказа (checkout).

DoD-сценарии (имена тестов строго из задания):
  happy:
    - checkout_creates_paid_order_with_fixed_prices
  unhappy:
    - partial_reserve_failure_returns_409
    - idempotency_returns_existing_order
    - b2b_unavailable_returns_503
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from jose import jwt

from app.main import app
from app.config import settings
from app.dependencies.db import get_db
from app.models.order import Order, OrderItem, OrderStatus

# ─── Константы ───────────────────────────────────────────────────────────────

_NOW = datetime.now(timezone.utc)
USER_ID = uuid.uuid4()
SKU_ID_1 = uuid.uuid4()
SKU_ID_2 = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()
PRODUCT_ID_2 = uuid.uuid4()
IDEM_KEY = uuid.uuid4()
ADDRESS_ID = uuid.uuid4()
PAYMENT_METHOD_ID = uuid.uuid4()


def _make_jwt(user_id: uuid.UUID = USER_ID) -> str:
    return jwt.encode(
        {"sub": str(user_id)},
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


AUTH_HEADERS = {
    "Authorization": f"Bearer {_make_jwt()}",
    "Idempotency-Key": str(IDEM_KEY),
}

CHECKOUT_BODY = {
    "address_id": str(ADDRESS_ID),
    "payment_method_id": str(PAYMENT_METHOD_ID),
}


# ─── Фабрики ─────────────────────────────────────────────────────────────────

def make_cart_item(sku_id: uuid.UUID = SKU_ID_1, quantity: int = 2) -> MagicMock:
    item = MagicMock()
    item.sku_id = sku_id
    item.quantity = quantity
    return item


def make_sku_response(
    sku_id: uuid.UUID = SKU_ID_1,
    product_id: uuid.UUID = PRODUCT_ID,
    name: str = "256GB Black",
    price: int = 12_999_000,
    active_quantity: int = 10,
) -> dict:
    """SKUPublicResponse — без вложенного product."""
    return {
        "id": str(sku_id),
        "product_id": str(product_id),
        "name": name,
        "price": price,
        "discount": 0,
        "stock_quantity": active_quantity,
        "active_quantity": active_quantity,
        "article": None,
        "images": [],
        "characteristics": [],
    }


def make_product_response(
    product_id: uuid.UUID = PRODUCT_ID,
    title: str = "iPhone 15 Pro",
    status: str = "MODERATED",
) -> dict:
    """ProductPublicResponse — статус товара для checkout-валидации."""
    return {
        "id": str(product_id),
        "seller_id": str(uuid.uuid4()),
        "title": title,
        "slug": "iphone-15-pro",
        "description": "desc",
        "category_id": str(uuid.uuid4()),
        "status": status,
        "images": [],
        "characteristics": [],
        "skus": [],
        "created_at": _NOW.isoformat(),
        "updated_at": _NOW.isoformat(),
    }


def make_order_item(
    sku_id: uuid.UUID = SKU_ID_1,
    product_id: uuid.UUID = PRODUCT_ID,
    unit_price: int = 12_999_000,
    quantity: int = 2,
) -> MagicMock:
    item = MagicMock(spec=OrderItem)
    item.id = uuid.uuid4()
    item.sku_id = sku_id
    item.product_id = product_id
    item.product_title = "iPhone 15 Pro"
    item.sku_name = "256GB Black"
    item.quantity = quantity
    item.unit_price = unit_price
    item.line_total = unit_price * quantity
    return item


def make_order(
    order_id: uuid.UUID | None = None,
    idempotency_key: uuid.UUID = IDEM_KEY,
    status: OrderStatus = OrderStatus.PAID,
) -> MagicMock:
    o = MagicMock(spec=Order)
    o.id = order_id or uuid.uuid4()
    o.user_id = USER_ID
    o.status = status
    o.total_amount = 12_999_000 * 3
    o.delivery_address = f"address:{ADDRESS_ID}"
    o.idempotency_key = idempotency_key
    o.created_at = _NOW
    o.updated_at = _NOW
    o.items = [make_order_item()]
    return o


# ─── Фикстуры ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def override_db():
    fake_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ─── Вспомогательные патчи ───────────────────────────────────────────────────

def _patch_cart(items: list[MagicMock] | None = None):
    cart_items = items or [
        make_cart_item(sku_id=SKU_ID_1, quantity=2),
        make_cart_item(sku_id=SKU_ID_2, quantity=1),
    ]
    return patch(
        "app.services.order_service.get_user_cart_items",
        new=AsyncMock(return_value=cart_items),
    )


def _patch_b2b_ok(
    skus: list[dict],
    products: list[dict] | None = None,
):
    if products is None:
        product_ids = {s["product_id"] for s in skus}
        products = [
            make_product_response(product_id=uuid.UUID(pid))
            for pid in product_ids
        ]
    return (
        patch(
            "app.services.order_service.get_products_by_sku_ids",
            new=AsyncMock(return_value=skus),
        ),
        patch(
            "app.services.order_service.get_public_products_batch",
            new=AsyncMock(return_value=products),
        ),
    )


def _patch_reserve_ok():
    return patch(
        "app.services.order_service.reserve",
        new=AsyncMock(return_value={"order_id": str(uuid.uuid4()), "status": "RESERVED"}),
    )


def _patch_persist(order: MagicMock):
    return patch(
        "app.services.order_service._persist_order",
        new=AsyncMock(return_value=order),
    )


def _patch_idempotency_miss():
    return patch(
        "app.services.order_service._get_order_by_idempotency_key",
        new=AsyncMock(return_value=None),
    )


def _patch_idempotency_hit(order: MagicMock):
    return patch(
        "app.services.order_service._get_order_by_idempotency_key",
        new=AsyncMock(return_value=order),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# HAPPY PATH
# ═══════════════════════════════════════════════════════════════════════════════

async def test_checkout_creates_paid_order_with_fixed_prices(override_db):
    sku1 = make_sku_response(sku_id=SKU_ID_1, price=12_999_000, active_quantity=10)
    sku2 = make_sku_response(
        sku_id=SKU_ID_2,
        product_id=PRODUCT_ID_2,
        name="256GB White",
        price=13_999_000,
        active_quantity=5,
    )
    products = [
        make_product_response(product_id=PRODUCT_ID),
        make_product_response(product_id=PRODUCT_ID_2),
    ]

    item1 = make_order_item(sku_id=SKU_ID_1, unit_price=12_999_000, quantity=2)
    item2 = make_order_item(sku_id=SKU_ID_2, unit_price=13_999_000, quantity=1)
    order = make_order()
    order.items = [item1, item2]
    order.total_amount = 12_999_000 * 2 + 13_999_000 * 1

    b2b_patches = _patch_b2b_ok([sku1, sku2], products)
    with (
        _patch_idempotency_miss(),
        _patch_cart(),
        b2b_patches[0],
        b2b_patches[1],
        _patch_reserve_ok(),
        _patch_persist(order),
    ):
        async with await _client() as c:
            resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY, headers=AUTH_HEADERS)

    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "PAID"

    for item in data["items"]:
        assert item["unit_price"] > 0
        assert item["line_total"] == item["unit_price"] * item["quantity"]

    expected_total = 12_999_000 * 2 + 13_999_000 * 1
    assert data["total"] == expected_total
    assert data["subtotal"] == expected_total
    assert data["buyer_id"] == str(USER_ID)
    assert data["delivery_address"] == f"address:{ADDRESS_ID}"
    assert all("name" in item for item in data["items"])


# ═══════════════════════════════════════════════════════════════════════════════
# UNHAPPY: PARTIAL RESERVE FAILURE → 409
# ═══════════════════════════════════════════════════════════════════════════════

async def test_partial_reserve_failure_returns_409(override_db):
    from app.services.b2b_client import B2BReserveFailedError

    sku1 = make_sku_response(sku_id=SKU_ID_1, active_quantity=10)
    sku2 = make_sku_response(sku_id=SKU_ID_2, product_id=PRODUCT_ID_2, active_quantity=5)
    products = [
        make_product_response(product_id=PRODUCT_ID),
        make_product_response(product_id=PRODUCT_ID_2),
    ]

    failed_items = [
        {
            "sku_id": str(SKU_ID_2),
            "requested": 1,
            "available": 0,
            "reason": "INSUFFICIENT_STOCK",
        }
    ]

    b2b_patches = _patch_b2b_ok([sku1, sku2], products)
    with (
        _patch_idempotency_miss(),
        _patch_cart(),
        b2b_patches[0],
        b2b_patches[1],
        patch(
            "app.services.order_service.reserve",
            new=AsyncMock(side_effect=B2BReserveFailedError(failed_items)),
        ),
    ):
        async with await _client() as c:
            resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY, headers=AUTH_HEADERS)

    assert resp.status_code == 409
    detail = resp.json()
    assert detail["code"] == "RESERVE_FAILED"
    assert len(detail["failed_items"]) >= 1
    assert detail["failed_items"][0]["sku_id"] == str(SKU_ID_2)


# ═══════════════════════════════════════════════════════════════════════════════
# UNHAPPY: IDEMPOTENCY → RETURN EXISTING ORDER
# ═══════════════════════════════════════════════════════════════════════════════

async def test_idempotency_returns_existing_order(override_db):
    existing = make_order(idempotency_key=IDEM_KEY)

    with (
        _patch_idempotency_hit(existing),
        patch("app.services.order_service.get_user_cart_items", new=AsyncMock()) as mock_cart,
        patch("app.services.order_service.get_products_by_sku_ids", new=AsyncMock()) as mock_b2b,
        patch("app.services.order_service.reserve", new=AsyncMock()) as mock_reserve,
    ):
        async with await _client() as c:
            resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY, headers=AUTH_HEADERS)

    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == str(existing.id)
    assert data["status"] == "PAID"

    mock_cart.assert_not_awaited()
    mock_b2b.assert_not_awaited()
    mock_reserve.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════════
# UNHAPPY: B2B UNAVAILABLE → 503
# ═══════════════════════════════════════════════════════════════════════════════

async def test_b2b_unavailable_returns_503(override_db):
    from app.services.b2b_client import B2BUnavailableError

    with (
        _patch_idempotency_miss(),
        _patch_cart(),
        patch(
            "app.services.order_service.get_products_by_sku_ids",
            new=AsyncMock(side_effect=B2BUnavailableError("connection refused")),
        ),
    ):
        async with await _client() as c:
            resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY, headers=AUTH_HEADERS)

    assert resp.status_code == 503
    detail = resp.json()
    assert detail["code"] == "B2B_UNAVAILABLE"


# ═══════════════════════════════════════════════════════════════════════════════
# ДОПОЛНИТЕЛЬНЫЕ ТЕСТЫ
# ═══════════════════════════════════════════════════════════════════════════════

async def test_checkout_requires_auth(override_db):
    async with await _client() as c:
        resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY)
    assert resp.status_code in (401, 403, 422)


async def test_checkout_requires_idempotency_key(override_db):
    headers = {"Authorization": f"Bearer {_make_jwt()}"}
    async with await _client() as c:
        resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY, headers=headers)
    assert resp.status_code == 422


async def test_checkout_validates_empty_cart(override_db):
    with (
        _patch_idempotency_miss(),
        patch(
            "app.services.order_service.get_user_cart_items",
            new=AsyncMock(return_value=[]),
        ),
    ):
        async with await _client() as c:
            resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"


async def test_checkout_returns_409_on_blocked_product(override_db):
    sku_blocked = make_sku_response(sku_id=SKU_ID_1, active_quantity=10)
    sku_ok = make_sku_response(sku_id=SKU_ID_2, product_id=PRODUCT_ID_2, active_quantity=5)
    # Товар SKU_ID_1 заблокирован — отсутствует в batch или status=BLOCKED
    products = [
        make_product_response(product_id=PRODUCT_ID, status="BLOCKED"),
        make_product_response(product_id=PRODUCT_ID_2),
    ]

    b2b_patches = _patch_b2b_ok([sku_blocked, sku_ok], products)
    with (
        _patch_idempotency_miss(),
        _patch_cart(),
        b2b_patches[0],
        b2b_patches[1],
        patch("app.services.order_service.reserve", new=AsyncMock()) as mock_reserve,
    ):
        async with await _client() as c:
            resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY, headers=AUTH_HEADERS)

    assert resp.status_code == 409
    detail = resp.json()
    assert detail["code"] == "RESERVE_FAILED"
    reasons = [fi["reason"] for fi in detail["failed_items"]]
    assert "PRODUCT_BLOCKED" in reasons
    mock_reserve.assert_not_awaited()


async def test_checkout_returns_409_on_insufficient_stock(override_db):
    sku1 = make_sku_response(sku_id=SKU_ID_1, active_quantity=10)
    sku2 = make_sku_response(sku_id=SKU_ID_2, product_id=PRODUCT_ID_2, active_quantity=0)
    products = [
        make_product_response(product_id=PRODUCT_ID),
        make_product_response(product_id=PRODUCT_ID_2),
    ]

    b2b_patches = _patch_b2b_ok([sku1, sku2], products)
    with (
        _patch_idempotency_miss(),
        _patch_cart(),
        b2b_patches[0],
        b2b_patches[1],
        patch("app.services.order_service.reserve", new=AsyncMock()) as mock_reserve,
    ):
        async with await _client() as c:
            resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY, headers=AUTH_HEADERS)

    assert resp.status_code == 409
    detail = resp.json()
    failed = detail["failed_items"]
    assert any(fi["reason"] in ("OUT_OF_STOCK", "INSUFFICIENT_STOCK") for fi in failed)
    mock_reserve.assert_not_awaited()


async def test_b2b_unavailable_on_reserve_returns_503(override_db):
    from app.services.b2b_client import B2BUnavailableError

    sku1 = make_sku_response(sku_id=SKU_ID_1, active_quantity=10)
    sku2 = make_sku_response(sku_id=SKU_ID_2, product_id=PRODUCT_ID_2, active_quantity=5)
    products = [
        make_product_response(product_id=PRODUCT_ID),
        make_product_response(product_id=PRODUCT_ID_2),
    ]

    b2b_patches = _patch_b2b_ok([sku1, sku2], products)
    with (
        _patch_idempotency_miss(),
        _patch_cart(),
        b2b_patches[0],
        b2b_patches[1],
        patch(
            "app.services.order_service.reserve",
            new=AsyncMock(side_effect=B2BUnavailableError("timeout")),
        ),
    ):
        async with await _client() as c:
            resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY, headers=AUTH_HEADERS)

    assert resp.status_code == 503
    assert resp.json()["code"] == "B2B_UNAVAILABLE"
