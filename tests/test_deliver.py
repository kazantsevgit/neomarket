"""
Тесты US-B2C-13: доставка заказа (POST /api/v1/orders/{id}/deliver).

DoD-сценарии (имена строго из задания):
  - delivered_status_triggers_fulfill_to_b2b
  - fulfill_failure_retried_asynchronously
  - repeated_fulfill_idempotent
"""

import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.config import settings
from app.dependencies.db import get_db
from app.models.order import Order, OrderItem, OrderStatus
from app.services.b2b_client import B2BUnavailableError

# ─── Константы ───────────────────────────────────────────────────────────────

_NOW = datetime.now(timezone.utc)
ORDER_ID = uuid.uuid4()
SKU_ID_1 = uuid.uuid4()
SKU_ID_2 = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()

SERVICE_KEY_HEADER = {"X-Service-Key": settings.B2B_SERVICE_KEY}


# ─── Фабрики ─────────────────────────────────────────────────────────────────

def make_order_item(sku_id: uuid.UUID = SKU_ID_1, quantity: int = 2) -> MagicMock:
    item = MagicMock(spec=OrderItem)
    item.id = uuid.uuid4()
    item.sku_id = sku_id
    item.product_id = PRODUCT_ID
    item.product_title = "iPhone 15 Pro"
    item.sku_name = "256GB Black"
    item.quantity = quantity
    item.unit_price = 12_999_000
    item.line_total = 12_999_000 * quantity
    return item


def make_order(
    order_id: uuid.UUID = ORDER_ID,
    order_status: OrderStatus = OrderStatus.DELIVERING,
) -> MagicMock:
    o = MagicMock(spec=Order)
    o.id = order_id
    o.user_id = uuid.uuid4()
    o.status = order_status
    o.total_amount = 12_999_000 * 2 + 12_999_000 * 1
    o.delivery_address = "г. Москва, ул. Тверская, д. 1"
    o.idempotency_key = uuid.uuid4()
    o.created_at = _NOW
    o.updated_at = _NOW
    o.items = [make_order_item(SKU_ID_1, 2), make_order_item(SKU_ID_2, 1)]
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


def _mock_db_with_order(order: MagicMock | None) -> AsyncMock:
    """Возвращает fake_db, чей execute().scalar_one_or_none() вернёт order."""
    db = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = order
    db.execute.return_value = scalar_result
    return db


# ═══════════════════════════════════════════════════════════════════════════════
# HAPPY PATH
# ═══════════════════════════════════════════════════════════════════════════════

async def test_delivered_status_triggers_fulfill_to_b2b(override_db):
    """
    Happy path: заказ в статусе DELIVERING, fulfill прошёл → статус DELIVERED,
    B2B вызван с корректными items.
    """
    order = make_order(order_status=OrderStatus.DELIVERING)
    db = _mock_db_with_order(order)
    app.dependency_overrides[get_db] = lambda: db

    with patch(
        "app.services.deliver_service.fulfill",
        new=AsyncMock(return_value={"fulfilled": True}),
    ) as mock_fulfill:
        async with await _client() as c:
            resp = await c.post(
                f"/api/v1/orders/{ORDER_ID}/deliver",
                headers=SERVICE_KEY_HEADER,
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "DELIVERED"
    assert data["id"] == str(ORDER_ID)
    assert len(data["items"]) == 2

    # fulfill вызван с правильными параметрами
    mock_fulfill.assert_awaited_once()
    call_args = mock_fulfill.await_args
    assert call_args is not None
    assert call_args.kwargs["order_id"] == ORDER_ID
    assert len(call_args.kwargs["items"]) == 2
    assert call_args.kwargs["items"][0]["sku_id"] == str(SKU_ID_1)
    assert call_args.kwargs["items"][0]["quantity"] == 2

    assert order.status == OrderStatus.DELIVERED
    db.commit.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════════
# UNHAPPY: FULFILL FAILURE → ORDER STAYS DELIVERED, ASYNC RETRY
# ═══════════════════════════════════════════════════════════════════════════════

async def test_fulfill_failure_retried_asynchronously(override_db, caplog):
    """
    B2B недоступен при fulfill → заказ остаётся DELIVERED,
    ошибка залогирована для асинхронного retry.
    """
    order = make_order(order_status=OrderStatus.DELIVERING)
    db = _mock_db_with_order(order)
    app.dependency_overrides[get_db] = lambda: db

    with patch(
        "app.services.deliver_service.fulfill",
        new=AsyncMock(side_effect=B2BUnavailableError("B2B timeout")),
    ):
        with caplog.at_level(logging.ERROR):
            async with await _client() as c:
                resp = await c.post(
                    f"/api/v1/orders/{ORDER_ID}/deliver",
                    headers=SERVICE_KEY_HEADER,
                )

    # Заказ доставлен — товар уже у покупателя
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "DELIVERED"
    assert order.status == OrderStatus.DELIVERED

    # Ошибка залогирована для async retry
    assert any("fulfill failed for order" in rec.message for rec in caplog.records)
    assert any("B2B timeout" in rec.message for rec in caplog.records)

    db.commit.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════════
# ИДЕМПОТЕНТНОСТЬ (B2B-level): повторный вызов → 200 без изменений
# ═══════════════════════════════════════════════════════════════════════════════

async def test_repeated_fulfill_idempotent(override_db):
    """
    B2B идемпотентен по order_id: повторный вызов fulfill с тем же
    order_id возвращает 200 без изменений. Проверяем через мок B2B.
    """
    order = make_order(order_status=OrderStatus.DELIVERING)
    db = _mock_db_with_order(order)
    app.dependency_overrides[get_db] = lambda: db

    with patch(
        "app.services.deliver_service.fulfill",
        new=AsyncMock(return_value={"fulfilled": True}),
    ) as mock_fulfill:
        async with await _client() as c:
            # ── Первый вызов ──────────────────────────────────────────────
            resp1 = await c.post(
                f"/api/v1/orders/{ORDER_ID}/deliver",
                headers=SERVICE_KEY_HEADER,
            )
            assert resp1.status_code == 200
            assert resp1.json()["status"] == "DELIVERED"

            # Сбрасываем статус обратно на DELIVERING для повторного вызова
            order.status = OrderStatus.DELIVERING

            # ── Второй вызов (тот же order_id) ────────────────────────────
            resp2 = await c.post(
                f"/api/v1/orders/{ORDER_ID}/deliver",
                headers=SERVICE_KEY_HEADER,
            )
            assert resp2.status_code == 200
            assert resp2.json()["status"] == "DELIVERED"

    # fulfill вызван дважды — B2B вернул 200 оба раза (идемпотентность)
    assert mock_fulfill.call_count == 2
    for call_args in mock_fulfill.await_args_list:
        assert call_args.kwargs["order_id"] == ORDER_ID


# ═══════════════════════════════════════════════════════════════════════════════
# UNHAPPY: WRONG STATUS → 409
# ═══════════════════════════════════════════════════════════════════════════════

async def test_deliver_non_delivering_status_returns_409(override_db):
    """Заказ в статусе PAID → 409 DELIVER_NOT_ALLOWED с current_status."""
    order = make_order(order_status=OrderStatus.PAID)
    db = _mock_db_with_order(order)
    app.dependency_overrides[get_db] = lambda: db

    async with await _client() as c:
        resp = await c.post(
            f"/api/v1/orders/{ORDER_ID}/deliver",
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 409
    detail = resp.json()
    assert detail["code"] == "DELIVER_NOT_ALLOWED"
    assert detail["current_status"] == "PAID"


# ═══════════════════════════════════════════════════════════════════════════════
# UNHAPPY: ORDER NOT FOUND → 404
# ═══════════════════════════════════════════════════════════════════════════════

async def test_deliver_nonexistent_order_returns_404(override_db):
    """Заказ не существует → 404 ORDER_NOT_FOUND."""
    db = _mock_db_with_order(None)
    app.dependency_overrides[get_db] = lambda: db

    async with await _client() as c:
        resp = await c.post(
            f"/api/v1/orders/{uuid.uuid4()}/deliver",
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 404
    assert resp.json()["code"] == "ORDER_NOT_FOUND"


# ═══════════════════════════════════════════════════════════════════════════════
# UNHAPPY: MISSING SERVICE KEY → 401
# ═══════════════════════════════════════════════════════════════════════════════

async def test_deliver_requires_service_key(override_db):
    """Без X-Service-Key → 401."""
    async with await _client() as c:
        resp = await c.post(f"/api/v1/orders/{ORDER_ID}/deliver")
    assert resp.status_code in (401,)
