"""
Тесты обработки событий от B2B (PRODUCT_BLOCKED / PRODUCT_DELETED / SKU_OUT_OF_STOCK).

DoD-сценарии:
  happy:
    - product_blocked_marks_cart_items_unavailable — PRODUCT_BLOCKED → все cart_items
      с этими sku_ids получают unavailable_reason
  unhappy:
    - orders_not_affected_by_product_blocked — заказы с теми же sku_ids не изменяются
    - idempotent_event_no_side_effects — повторное событие с тем же idempotency_key
      → 200 без эффекта
    - missing_service_key_returns_401 — запрос без X-Service-Key → 401
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.dependencies.db import get_db
from app.models.cart import CartItem as CartItemDB
from app.models.event_idempotency import EventIdempotencyKey

# ─── Константы ───────────────────────────────────────────────────────────────

_NOW = datetime.now(timezone.utc)
IDEM_KEY = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()
SKU_1 = uuid.uuid4()
SKU_2 = uuid.uuid4()
SKU_IDS = [SKU_1, SKU_2]

SERVICE_KEY = "test-b2b-service-key"
SERVICE_KEY_HEADER = {"X-Service-Key": SERVICE_KEY}

BASE_EVENT = {
    "idempotency_key": str(IDEM_KEY),
    "event": "PRODUCT_BLOCKED",
    "product_id": str(PRODUCT_ID),
    "sku_ids": [str(s) for s in SKU_IDS],
    "reason": "Описание не соответствует товару",
    "date": _NOW.isoformat(),
}

# ─── Фикстуры ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def override_service_key(monkeypatch):
    monkeypatch.setattr("app.config.settings.B2B_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.setattr("app.routers.events.settings.B2B_SERVICE_KEY", SERVICE_KEY)


@pytest.fixture(autouse=True)
def override_db():
    fake_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def make_idempotency_record(event: str = "PRODUCT_BLOCKED") -> MagicMock:
    r = MagicMock(spec=EventIdempotencyKey)
    r.idempotency_key = IDEM_KEY
    r.event = event
    r.product_id = PRODUCT_ID
    return r


# ─── Happy path ──────────────────────────────────────────────────────────────


async def test_product_blocked_marks_cart_items_unavailable(override_db):
    """
    happy: product_blocked_marks_cart_items_unavailable
    PRODUCT_BLOCKED → batch UPDATE cart_items с этими sku_ids.
    """
    db: AsyncMock = override_db
    db.get.return_value = None  # idempotency not found — proceed

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=BASE_EVENT,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data == {"accepted": True}

    # Проверяем batch UPDATE
    db.execute.assert_awaited_once()
    call_args = db.execute.call_args[0]
    sql = str(call_args[0])  # text of the UPDATE statement
    assert "UPDATE cart_items" in sql
    assert "unavailable_reason" in sql
    assert "sku_ids" in sql or "ANY" in sql

    # Проверяем idempotency запись
    db.add.assert_called_once()
    added = db.add.call_args[0][0]
    assert isinstance(added, EventIdempotencyKey)
    assert added.idempotency_key == IDEM_KEY
    assert added.event == "PRODUCT_BLOCKED"

    db.commit.assert_awaited_once()


async def test_orders_not_affected_by_product_blocked(override_db):
    """
    unhappy: orders_not_affected_by_product_blocked
    Событие не трогает таблицу orders.
    """
    db: AsyncMock = override_db
    db.get.return_value = None

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=BASE_EVENT,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 200

    # UPDATE только по cart_items, не по orders/order_items
    execute_sql = str(db.execute.call_args[0][0])
    assert "cart_items" in execute_sql
    assert "orders" not in execute_sql
    assert "order_items" not in execute_sql


# ─── Unhappy path ────────────────────────────────────────────────────────────


async def test_idempotent_event_no_side_effects(override_db):
    """
    unhappy: idempotent_event_no_side_effects
    Повторное событие с тем же idempotency_key → 200, execute/add не вызываются.
    """
    db: AsyncMock = override_db
    db.get.return_value = make_idempotency_record()

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=BASE_EVENT,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data == {"accepted": True}

    # Никаких side effects
    db.execute.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


async def test_missing_service_key_returns_401(override_db):
    """
    unhappy: missing_service_key_returns_401
    Запрос без X-Service-Key → 401.
    """
    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=BASE_EVENT,
            # Без SERVICE_KEY_HEADER
        )

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_wrong_service_key_returns_401(override_db):
    """
    unhappy: неверный X-Service-Key → 401.
    """
    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=BASE_EVENT,
            headers={"X-Service-Key": "wrong-key"},
        )

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"
