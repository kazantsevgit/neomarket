"""
Тесты US-ORD-03: отмена заказа (POST /api/v1/orders/{id}/cancel).

DoD-сценарии (имена строго из задания):
  - cancel_paid_order_transitions_to_cancelled
  - unreserve_failure_transitions_to_cancel_pending
  - cancel_assembling_order_returns_409
  - other_user_order_returns_404
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
from app.services.b2b_client import B2BUnavailableError

# ─── Константы ───────────────────────────────────────────────────────────────

_NOW = datetime.now(timezone.utc)
USER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()
ORDER_ID = uuid.uuid4()
SKU_ID_1 = uuid.uuid4()
SKU_ID_2 = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()


def _make_jwt(user_id: uuid.UUID = USER_ID) -> str:
    return jwt.encode(
        {"sub": str(user_id)},
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


AUTH_HEADERS = {"Authorization": f"Bearer {_make_jwt()}"}
OTHER_AUTH_HEADERS = {"Authorization": f"Bearer {_make_jwt(OTHER_USER_ID)}"}


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
    user_id: uuid.UUID = USER_ID,
    order_status: OrderStatus = OrderStatus.PAID,
) -> MagicMock:
    o = MagicMock(spec=Order)
    o.id = order_id
    o.user_id = user_id
    o.status = order_status
    o.total_amount = 12_999_000 * 2
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

async def test_cancel_paid_order_transitions_to_cancelled(override_db):
    """
    Happy path: заказ в статусе PAID, unreserve прошёл → статус CANCELLED.
    """
    order = make_order(order_status=OrderStatus.PAID)
    db = _mock_db_with_order(order)
    app.dependency_overrides[get_db] = lambda: db

    with patch(
        "app.services.cancel_service.unreserve",
        new=AsyncMock(return_value={"order_id": str(ORDER_ID), "status": "UNRESERVED"}),
    ):
        async with await _client() as c:
            resp = await c.post(
                f"/api/v1/orders/{ORDER_ID}/cancel",
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 200
    # Статус должен смениться на CANCELLED
    assert order.status == OrderStatus.CANCELLED
    db.commit.assert_awaited_once()


async def test_cancel_created_order_transitions_to_cancelled(override_db):
    """
    CREATED тоже допустим для отмены (доп. покрытие).
    """
    order = make_order(order_status=OrderStatus.CREATED)
    db = _mock_db_with_order(order)
    app.dependency_overrides[get_db] = lambda: db

    with patch(
        "app.services.cancel_service.unreserve",
        new=AsyncMock(return_value={"order_id": str(ORDER_ID), "status": "UNRESERVED"}),
    ):
        async with await _client() as c:
            resp = await c.post(
                f"/api/v1/orders/{ORDER_ID}/cancel",
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 200
    assert order.status == OrderStatus.CANCELLED


# ═══════════════════════════════════════════════════════════════════════════════
# UNHAPPY: UNRESERVE FAILURE → CANCEL_PENDING
# ═══════════════════════════════════════════════════════════════════════════════

async def test_unreserve_failure_transitions_to_cancel_pending(override_db):
    """
    B2B недоступен при unreserve → статус CANCEL_PENDING.
    Покупатель получает 200 (намерение принято), retry асинхронно.
    """
    order = make_order(order_status=OrderStatus.PAID)
    db = _mock_db_with_order(order)
    app.dependency_overrides[get_db] = lambda: db

    with patch(
        "app.services.cancel_service.unreserve",
        new=AsyncMock(side_effect=B2BUnavailableError("connection timeout")),
    ):
        async with await _client() as c:
            resp = await c.post(
                f"/api/v1/orders/{ORDER_ID}/cancel",
                headers=AUTH_HEADERS,
            )

    # Покупатель получает 200 — намерение принято
    assert resp.status_code == 200
    # Статус CANCEL_PENDING, не CANCELLED
    assert order.status == OrderStatus.CANCEL_PENDING
    # Заказ всё равно сохраняется
    db.commit.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════════
# UNHAPPY: WRONG STATUS → 409
# ═══════════════════════════════════════════════════════════════════════════════

async def test_cancel_assembling_order_returns_409(override_db):
    """
    Заказ в статусе ASSEMBLING → 409 CANCEL_NOT_ALLOWED с current_status.
    unreserve не вызывается.
    """
    order = make_order(order_status=OrderStatus.ASSEMBLING)
    db = _mock_db_with_order(order)
    app.dependency_overrides[get_db] = lambda: db

    with patch(
        "app.services.cancel_service.unreserve",
        new=AsyncMock(),
    ) as mock_unreserve:
        async with await _client() as c:
            resp = await c.post(
                f"/api/v1/orders/{ORDER_ID}/cancel",
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 409
    detail = resp.json()
    assert detail["code"] == "CANCEL_NOT_ALLOWED"
    assert detail["current_status"] == "ASSEMBLING"
    mock_unreserve.assert_not_awaited()


@pytest.mark.parametrize("bad_status", [
    OrderStatus.DELIVERING,
    OrderStatus.DELIVERED,
    OrderStatus.CANCELLED,
    OrderStatus.CANCEL_PENDING,
])
async def test_cancel_non_cancellable_statuses_return_409(override_db, bad_status):
    """Все статусы кроме CREATED/PAID → 409."""
    order = make_order(order_status=bad_status)
    db = _mock_db_with_order(order)
    app.dependency_overrides[get_db] = lambda: db

    async with await _client() as c:
        resp = await c.post(
            f"/api/v1/orders/{ORDER_ID}/cancel",
            headers=AUTH_HEADERS,
        )

    assert resp.status_code == 409
    assert resp.json()["code"] == "CANCEL_NOT_ALLOWED"


# ═══════════════════════════════════════════════════════════════════════════════
# UNHAPPY: IDOR → 404
# ═══════════════════════════════════════════════════════════════════════════════

async def test_other_user_order_returns_404(override_db):
    """
    IDOR: заказ принадлежит другому пользователю → 404 ORDER_NOT_FOUND (не 403).
    Факт существования заказа не раскрывается.
    """
    # Заказ принадлежит USER_ID, но запрашивает OTHER_USER_ID
    order = make_order(user_id=USER_ID, order_status=OrderStatus.PAID)
    db = _mock_db_with_order(order)
    app.dependency_overrides[get_db] = lambda: db

    async with await _client() as c:
        resp = await c.post(
            f"/api/v1/orders/{ORDER_ID}/cancel",
            headers=OTHER_AUTH_HEADERS,  # другой пользователь
        )

    assert resp.status_code == 404
    assert resp.json()["code"] == "ORDER_NOT_FOUND"


async def test_nonexistent_order_returns_404(override_db):
    """Заказ не существует → 404 ORDER_NOT_FOUND."""
    db = _mock_db_with_order(None)
    app.dependency_overrides[get_db] = lambda: db

    async with await _client() as c:
        resp = await c.post(
            f"/api/v1/orders/{uuid.uuid4()}/cancel",
            headers=AUTH_HEADERS,
        )

    assert resp.status_code == 404
    assert resp.json()["code"] == "ORDER_NOT_FOUND"


async def test_cancel_requires_auth(override_db):
    """Без JWT → 403."""
    async with await _client() as c:
        resp = await c.post(f"/api/v1/orders/{ORDER_ID}/cancel")
    assert resp.status_code in (401, 403)
