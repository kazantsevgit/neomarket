"""
Тесты US-ORD-01: оформление заказа (checkout).

DoD-сценарии (имена тестов строго из задания):
  happy:
    - checkout_creates_paid_order_with_fixed_prices
  unhappy:
    - partial_reserve_failure_returns_409
    - idempotency_returns_existing_order
    - b2b_unavailable_returns_503

Дополнительные тесты:
    - checkout_validates_empty_items
    - checkout_returns_409_on_blocked_product
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
from app.services.b2b_client import B2BReserveFailedError, B2BUnavailableError

# ─── Константы ───────────────────────────────────────────────────────────────

_NOW = datetime.now(timezone.utc)
USER_ID = uuid.uuid4()
SKU_ID_1 = uuid.uuid4()
SKU_ID_2 = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()
IDEM_KEY = uuid.uuid4()


def _make_jwt(user_id: uuid.UUID = USER_ID) -> str:
    return jwt.encode(
        {"sub": str(user_id)},
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


AUTH_HEADERS = {"Authorization": f"Bearer {_make_jwt()}"}

CHECKOUT_BODY = {
    "idempotency_key": str(IDEM_KEY),
    "items": [
        {"sku_id": str(SKU_ID_1), "quantity": 2},
        {"sku_id": str(SKU_ID_2), "quantity": 1},
    ],
    "delivery_address": "г. Москва, ул. Тверская, д. 1",
}


# ─── Фабрики ─────────────────────────────────────────────────────────────────

def make_sku_response(
    sku_id: uuid.UUID = SKU_ID_1,
    product_id: uuid.UUID = PRODUCT_ID,
    name: str = "256GB Black",
    price: int = 12_999_000,
    active_quantity: int = 10,
    product_status: str = "MODERATED",
    product_deleted: bool = False,
    product_title: str = "iPhone 15 Pro",
) -> dict:
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
        "product": {
            "id": str(product_id),
            "title": product_title,
            "status": product_status,
            "deleted": product_deleted,
        },
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
    o.status = status
    o.total_amount = 12_999_000 * 3
    o.delivery_address = "г. Москва, ул. Тверская, д. 1"
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

def _patch_b2b_ok(skus: list[dict]):
    """Мок get_products_by_sku_ids — возвращает список SKU-словарей."""
    return patch(
        "app.services.order_service.get_products_by_sku_ids",
        new=AsyncMock(return_value=skus),
    )


def _patch_reserve_ok():
    return patch(
        "app.services.order_service.reserve",
        new=AsyncMock(return_value={"order_id": str(uuid.uuid4()), "status": "RESERVED"}),
    )


def _patch_persist(order: MagicMock):
    """Мок _persist_order — возвращает готовый объект Order."""
    return patch(
        "app.services.order_service._persist_order",
        new=AsyncMock(return_value=order),
    )


def _patch_idempotency_miss():
    """Мок _get_order_by_idempotency_key — ничего не найдено (новый ключ)."""
    return patch(
        "app.services.order_service._get_order_by_idempotency_key",
        new=AsyncMock(return_value=None),
    )


def _patch_idempotency_hit(order: MagicMock):
    """Мок _get_order_by_idempotency_key — заказ найден."""
    return patch(
        "app.services.order_service._get_order_by_idempotency_key",
        new=AsyncMock(return_value=order),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# HAPPY PATH
# ═══════════════════════════════════════════════════════════════════════════════

async def test_checkout_creates_paid_order_with_fixed_prices(override_db):
    """
    Happy path: checkout создаёт заказ в статусе PAID.
    unit_price зафиксирован в OrderItem — берётся из B2B в момент checkout,
    а не при последующем запросе деталей.
    """
    sku1 = make_sku_response(sku_id=SKU_ID_1, price=12_999_000, active_quantity=10)

    sku2 = make_sku_response(
        sku_id=SKU_ID_2,
        name="256GB White",
        price=13_999_000,
        active_quantity=5,
    )

    # Ожидаемый заказ — unit_price зафиксирован
    item1 = make_order_item(sku_id=SKU_ID_1, unit_price=12_999_000, quantity=2)
    item2 = make_order_item(sku_id=SKU_ID_2, unit_price=13_999_000, quantity=1)
    order = make_order()
    order.items = [item1, item2]
    order.total_amount = 12_999_000 * 2 + 13_999_000 * 1

    with (
        _patch_idempotency_miss(),
        _patch_b2b_ok([sku1, sku2]),
        _patch_reserve_ok(),
        _patch_persist(order),
    ):
        async with await _client() as c:
            resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY, headers=AUTH_HEADERS)

    assert resp.status_code == 201
    data = resp.json()

    # Заказ PAID
    assert data["status"] == "PAID"

    # unit_price зафиксирован из B2B — не NULL, не 0
    for item in data["items"]:
        assert item["unit_price"] > 0, "unit_price должен быть зафиксирован в OrderItem"
        assert item["line_total"] == item["unit_price"] * item["quantity"]

    # Итоговая сумма
    expected_total = 12_999_000 * 2 + 13_999_000 * 1
    assert data["total_amount"] == expected_total


# ═══════════════════════════════════════════════════════════════════════════════
# UNHAPPY: PARTIAL RESERVE FAILURE → 409
# ═══════════════════════════════════════════════════════════════════════════════

async def test_partial_reserve_failure_returns_409(override_db):
    """
    Unhappy: B2B вернул 409 при резервировании хотя бы одного SKU.
    B2C должен вернуть 409 RESERVE_FAILED с failed_items.
    Заказ не создаётся (all-or-nothing).
    """
    sku1 = make_sku_response(sku_id=SKU_ID_1, active_quantity=10)
    sku2 = make_sku_response(sku_id=SKU_ID_2, active_quantity=5)

    failed_items = [
        {
            "sku_id": str(SKU_ID_2),
            "requested": 1,
            "available": 0,
            "reason": "INSUFFICIENT_STOCK",
        }
    ]

    with (
        _patch_idempotency_miss(),
        _patch_b2b_ok([sku1, sku2]),
        patch(
            "app.services.order_service.reserve",
            new=AsyncMock(side_effect=B2BReserveFailedError(failed_items)),
        ),
    ):
        async with await _client() as c:
            resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY, headers=AUTH_HEADERS)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "RESERVE_FAILED"
    assert len(detail["failed_items"]) >= 1
    assert detail["failed_items"][0]["sku_id"] == str(SKU_ID_2)


# ═══════════════════════════════════════════════════════════════════════════════
# UNHAPPY: IDEMPOTENCY → RETURN EXISTING ORDER
# ═══════════════════════════════════════════════════════════════════════════════

async def test_idempotency_returns_existing_order(override_db):
    """
    Повторный POST с тем же idempotency_key должен вернуть существующий заказ.
    B2B reserve НЕ вызывается повторно — деньги не списываются дважды.
    """
    existing = make_order(idempotency_key=IDEM_KEY)

    with (
        _patch_idempotency_hit(existing),
        patch(
            "app.services.order_service.get_products_by_sku_ids",
            new=AsyncMock(),
        ) as mock_b2b,
        patch(
            "app.services.order_service.reserve",
            new=AsyncMock(),
        ) as mock_reserve,
    ):
        async with await _client() as c:
            resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY, headers=AUTH_HEADERS)

    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == str(existing.id)
    assert data["status"] == "PAID"

    # B2B не должен вызываться при idempotent-запросе
    mock_b2b.assert_not_awaited()
    mock_reserve.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════════
# UNHAPPY: B2B UNAVAILABLE → 503
# ═══════════════════════════════════════════════════════════════════════════════

async def test_b2b_unavailable_returns_503(override_db):
    """
    B2B недоступен (таймаут / ConnectionError) → B2C должен вернуть 503.
    Заказ не создаётся.
    """
    with (
        _patch_idempotency_miss(),
        patch(
            "app.services.order_service.get_products_by_sku_ids",
            new=AsyncMock(side_effect=B2BUnavailableError("connection refused")),
        ),
    ):
        async with await _client() as c:
            resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY, headers=AUTH_HEADERS)

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["code"] == "B2B_UNAVAILABLE"


# ═══════════════════════════════════════════════════════════════════════════════
# ДОПОЛНИТЕЛЬНЫЕ ТЕСТЫ
# ═══════════════════════════════════════════════════════════════════════════════

async def test_checkout_requires_auth(override_db):
    """Без JWT → 403 (FastAPI HTTPBearer не пропускает)."""
    async with await _client() as c:
        resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY)
    assert resp.status_code == 403


async def test_checkout_validates_empty_items(override_db):
    """Пустой items → 422 (Pydantic min_length=1)."""
    body = {**CHECKOUT_BODY, "items": []}
    async with await _client() as c:
        resp = await c.post("/api/v1/orders", json=body, headers=AUTH_HEADERS)
    assert resp.status_code == 422


async def test_checkout_returns_409_on_blocked_product(override_db):
    """
    Товар заблокирован модератором → 409 RESERVE_FAILED ещё до вызова reserve.
    B2B reserve НЕ вызывается.
    """
    sku_blocked = make_sku_response(
        sku_id=SKU_ID_1,
        product_status="BLOCKED",
        active_quantity=10,
    )
    sku_ok = make_sku_response(sku_id=SKU_ID_2, active_quantity=5)

    with (
        _patch_idempotency_miss(),
        _patch_b2b_ok([sku_blocked, sku_ok]),
        patch(
            "app.services.order_service.reserve",
            new=AsyncMock(),
        ) as mock_reserve,
    ):
        async with await _client() as c:
            resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY, headers=AUTH_HEADERS)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "RESERVE_FAILED"
    reasons = [fi["reason"] for fi in detail["failed_items"]]
    assert "PRODUCT_BLOCKED" in reasons

    # reserve не вызывался — проверка на шаге 3 отсекла запрос
    mock_reserve.assert_not_awaited()


async def test_checkout_returns_409_on_insufficient_stock(override_db):
    """
    active_quantity меньше запрошенного → 409 INSUFFICIENT_STOCK.
    """
    # SKU_2: доступно 0 штук
    sku1 = make_sku_response(sku_id=SKU_ID_1, active_quantity=10)
    sku2 = make_sku_response(sku_id=SKU_ID_2, active_quantity=0)

    with (
        _patch_idempotency_miss(),
        _patch_b2b_ok([sku1, sku2]),
        patch("app.services.order_service.reserve", new=AsyncMock()) as mock_reserve,
    ):
        async with await _client() as c:
            resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY, headers=AUTH_HEADERS)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    failed = detail["failed_items"]
    assert any(fi["reason"] in ("OUT_OF_STOCK", "INSUFFICIENT_STOCK") for fi in failed)
    mock_reserve.assert_not_awaited()


async def test_b2b_unavailable_on_reserve_returns_503(override_db):
    """
    B2B недоступен на шаге reserve (после успешного get_products) → 503.
    """
    sku1 = make_sku_response(sku_id=SKU_ID_1, active_quantity=10)
    sku2 = make_sku_response(sku_id=SKU_ID_2, active_quantity=5)

    with (
        _patch_idempotency_miss(),
        _patch_b2b_ok([sku1, sku2]),
        patch(
            "app.services.order_service.reserve",
            new=AsyncMock(side_effect=B2BUnavailableError("timeout")),
        ),
    ):
        async with await _client() as c:
            resp = await c.post("/api/v1/orders", json=CHECKOUT_BODY, headers=AUTH_HEADERS)

    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "B2B_UNAVAILABLE"