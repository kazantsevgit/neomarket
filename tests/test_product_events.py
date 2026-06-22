"""
Тесты US-ORD-04: обработка событий от B2B (PRODUCT_BLOCKED / PRODUCT_DELETED / SKU_OUT_OF_STOCK).

DoD-сценарии:
  happy:
    - product_blocked_marks_cart_items_unavailable
  unhappy:
    - orders_not_affected_by_product_blocked
    - idempotent_event_no_side_effects       (409 при дубликате)
    - missing_service_key_returns_401
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies.db import get_db
from app.main import app
from app.models.event_idempotency import EventIdempotencyKey

# ─── Константы ───────────────────────────────────────────────────────────────

_NOW = datetime.now(timezone.utc)
IDEM_KEY   = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()
SKU_1      = uuid.uuid4()
SKU_2      = uuid.uuid4()

SERVICE_KEY        = "test-b2b-service-key"
SERVICE_KEY_HEADER = {"X-Service-Key": SERVICE_KEY}
URL = "/api/v1/b2b/events"

BASE_EVENT = {
    "event_type":       "PRODUCT_BLOCKED",
    "idempotency_key":  str(IDEM_KEY),
    "occurred_at":      _NOW.isoformat(),
    "payload": {
        "product_id": str(PRODUCT_ID),
    },
}

SKU_OUT_OF_STOCK_EVENT = {
    "event_type":       "SKU_OUT_OF_STOCK",
    "idempotency_key":  str(uuid.uuid4()),
    "occurred_at":      _NOW.isoformat(),
    "payload": {
        "sku_id":             str(SKU_1),
        "product_id":         str(PRODUCT_ID),
        "available_quantity": 0,
    },
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
    PRODUCT_BLOCKED → 202, batch UPDATE cart_items, idempotency-запись сохранена.
    """
    db: AsyncMock = override_db
    db.get.return_value = None  # idempotency not found — proceed

    async with await _client() as client:
        resp = await client.post(URL, json=BASE_EVENT, headers=SERVICE_KEY_HEADER)

    assert resp.status_code == 202
    assert resp.json() == {"accepted": True}

    # Batch UPDATE выполнен
    db.execute.assert_awaited_once()
    sql = str(db.execute.call_args[0][0])
    assert "UPDATE cart_items" in sql
    assert "unavailable_reason" in sql

    # Idempotency-запись добавлена
    db.add.assert_called_once()
    added = db.add.call_args[0][0]
    assert isinstance(added, EventIdempotencyKey)
    assert added.event == "PRODUCT_BLOCKED"

    db.commit.assert_awaited_once()


async def test_orders_not_affected_by_product_blocked(override_db):
    """
    unhappy: orders_not_affected_by_product_blocked
    Событие обновляет только cart_items, таблица orders не трогается.
    """
    db: AsyncMock = override_db
    db.get.return_value = None

    async with await _client() as client:
        resp = await client.post(URL, json=BASE_EVENT, headers=SERVICE_KEY_HEADER)

    assert resp.status_code == 202

    sql = str(db.execute.call_args[0][0])
    assert "cart_items" in sql
    assert "orders" not in sql
    assert "order_items" not in sql


# ─── Unhappy path ─────────────────────────────────────────────────────────────

async def test_idempotent_event_no_side_effects(override_db):
    """
    unhappy: idempotent_event_no_side_effects
    Повторное событие с тем же idempotency_key → 409, никаких side effects.
    """
    db: AsyncMock = override_db
    db.get.return_value = make_idempotency_record()  # уже обработано

    async with await _client() as client:
        resp = await client.post(URL, json=BASE_EVENT, headers=SERVICE_KEY_HEADER)

    assert resp.status_code == 409
    assert resp.json()["code"] == "CONFLICT"

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
        resp = await client.post(URL, json=BASE_EVENT)  # без заголовка

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_wrong_service_key_returns_401(override_db):
    """unhappy: неверный X-Service-Key → 401."""
    async with await _client() as client:
        resp = await client.post(URL, json=BASE_EVENT, headers={"X-Service-Key": "wrong"})

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_sku_out_of_stock_marks_single_sku(override_db):
    """
    SKU_OUT_OF_STOCK: обновляется только конкретный sku_id, не весь product.
    """
    db: AsyncMock = override_db
    db.get.return_value = None

    async with await _client() as client:
        resp = await client.post(URL, json=SKU_OUT_OF_STOCK_EVENT, headers=SERVICE_KEY_HEADER)

    assert resp.status_code == 202

    sql = str(db.execute.call_args[0][0])
    assert "cart_items" in sql
    assert "sku_id" in sql
    # SKU_OUT_OF_STOCK не использует subquery по product
    assert "SELECT id FROM skus" not in sql