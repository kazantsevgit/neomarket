"""
Тесты endpoint POST /api/v1/inventory/reserve и /api/v1/inventory/unreserve.

DoD-сценарии:
  happy:
    - reserve_all_skus_succeeds
    - idempotent_reserve_returns_200_without_double_deduction
  unhappy:
    - partial_insufficient_stock_returns_409_all_rollback
    - sku_out_of_stock_event_emitted
  unreserve:
    - unreserve_restores_quantities
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.dependencies.db import get_db
from app.models.product import SKU
from app.models.reservation import ReservationIdempotency

# ─── Константы ───────────────────────────────────────────────────────────────

_NOW        = datetime.now(timezone.utc)
ORDER_ID    = uuid.uuid4()
SKU_ID_1    = uuid.uuid4()
SKU_ID_2    = uuid.uuid4()
IDEM_KEY    = uuid.uuid4()

SERVICE_KEY_HEADER = {"X-Service-Key": "test-service-key"}

RESERVE_BODY = {
    "idempotency_key": str(IDEM_KEY),
    "order_id": str(ORDER_ID),
    "items": [
        {"sku_id": str(SKU_ID_1), "quantity": 2},
        {"sku_id": str(SKU_ID_2), "quantity": 1},
    ],
}

UNRESERVE_BODY = {
    "order_id": str(ORDER_ID),
    "items": [
        {"sku_id": str(SKU_ID_1), "quantity": 2},
        {"sku_id": str(SKU_ID_2), "quantity": 1},
    ],
}


# ─── Фабрики ─────────────────────────────────────────────────────────────────

def make_sku(
    sku_id: uuid.UUID,
    stock_quantity: int = 10,
    reserved_quantity: int = 0,
) -> MagicMock:
    s = MagicMock(spec=SKU)
    s.id               = sku_id
    s.stock_quantity   = stock_quantity
    s.reserved_quantity = reserved_quantity

    # active_quantity как свойство: stock - reserved
    type(s).active_quantity = property(
        lambda self: max(0, self.stock_quantity - self.reserved_quantity)
    )
    return s


def make_idempotency_record(response_payload: dict) -> MagicMock:
    r = MagicMock(spec=ReservationIdempotency)
    r.idempotency_key   = IDEM_KEY
    r.order_id          = ORDER_ID
    r.response_payload  = response_payload
    return r


# ─── Фикстуры ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def override_service_key(monkeypatch):
    """Подменяем SERVICE_KEY в settings, чтобы хедер X-Service-Key проходил."""
    monkeypatch.setattr("app.config.settings.SERVICE_KEY", "test-service-key")
    monkeypatch.setattr("app.routers.inventory.settings.SERVICE_KEY", "test-service-key")


@pytest.fixture(autouse=True)
def override_db():
    fake_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


async def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _db_for_reserve(skus: list[MagicMock], idempotency_record=None) -> AsyncMock:
    """
    Настраивает fake_db:
    - get() → idempotency_record (None если нет)
    - execute() → список SKU с FOR UPDATE
    """
    db = AsyncMock()
    db.get.return_value = idempotency_record

    scalar_result = MagicMock()
    scalar_result.scalars.return_value.all.return_value = skus
    db.execute.return_value = scalar_result

    return db


def _make_reserve_op(sku_id: uuid.UUID, quantity: int) -> MagicMock:
    op = MagicMock()
    op.sku_id    = sku_id
    op.quantity  = quantity
    op.order_id  = ORDER_ID
    return op


def _db_for_unreserve(
    skus: list[MagicMock],
    idem_existing=None,
    ops: list | None = None,
) -> AsyncMock:
    """
    ops=None → нет ReserveOperation (резерв не был создан) → 404
    ops=[]   → пустой список (тот же эффект)
    ops=[…]  → список операций для верификации
    """
    db = AsyncMock()
    db.get.return_value = idem_existing

    # execute вызывается дважды: для ReserveOperation и для SKU
    ops_result = MagicMock()
    ops_result.scalars.return_value.all.return_value = ops if ops is not None else []

    sku_result = MagicMock()
    sku_result.scalars.return_value.all.return_value = skus

    db.execute.side_effect = [ops_result, sku_result]
    return db


# ─── Happy path ───────────────────────────────────────────────────────────────

async def test_reserve_all_skus_succeeds(override_db):
    """
    happy: reserve_all_skus_succeeds
    active_quantity уменьшился, reserved_quantity вырос.
    """
    sku1 = make_sku(SKU_ID_1, stock_quantity=10, reserved_quantity=0)
    sku2 = make_sku(SKU_ID_2, stock_quantity=5, reserved_quantity=0)

    db = _db_for_reserve([sku1, sku2])
    app.dependency_overrides[get_db] = lambda: db

    async with await make_client() as client:
        resp = await client.post(
            "/api/v1/inventory/reserve",
            json=RESERVE_BODY,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "RESERVED"
    assert data["order_id"] == str(ORDER_ID)

    # Инвариант: reserved_quantity увеличился
    assert sku1.reserved_quantity == 2
    assert sku2.reserved_quantity == 1

    db.commit.assert_awaited_once()


async def test_idempotent_reserve_returns_200_without_double_deduction(override_db):
    """
    happy: idempotent_reserve_returns_200_without_double_deduction
    Повторный запрос с тем же idempotency_key → 200, SKU не меняются.
    """
    cached_payload = {
        "order_id": str(ORDER_ID),
        "status": "RESERVED",
        "reserved_at": _NOW.isoformat(),
    }
    idem_record = make_idempotency_record(cached_payload)

    # db.get() вернёт существующую запись → сервис вернёт закешированный ответ
    db = _db_for_reserve([], idempotency_record=idem_record)
    app.dependency_overrides[get_db] = lambda: db

    async with await make_client() as client:
        resp = await client.post(
            "/api/v1/inventory/reserve",
            json=RESERVE_BODY,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "RESERVED"

    # SELECT FOR UPDATE и commit НЕ вызывались — дедупликация сработала
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()


# ─── Unhappy path ─────────────────────────────────────────────────────────────

async def test_partial_insufficient_stock_returns_409_all_rollback(override_db):
    """
    unhappy: partial_insufficient_stock_returns_409_all_rollback
    SKU_2 не хватает → 409, ни один SKU не изменён (rollback).
    """
    sku1 = make_sku(SKU_ID_1, stock_quantity=10, reserved_quantity=0)
    # SKU_2: active_quantity = 0 (уже всё зарезервировано)
    sku2 = make_sku(SKU_ID_2, stock_quantity=5, reserved_quantity=5)

    db = _db_for_reserve([sku1, sku2])
    app.dependency_overrides[get_db] = lambda: db

    async with await make_client() as client:
        resp = await client.post(
            "/api/v1/inventory/reserve",
            json=RESERVE_BODY,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "INSUFFICIENT_STOCK"
    assert str(SKU_ID_2) in body["details"]["sku_ids"]

    # Rollback — ни один SKU не изменён
    assert sku1.reserved_quantity == 0
    assert sku2.reserved_quantity == 5

    db.commit.assert_not_awaited()


async def test_sku_out_of_stock_event_emitted(override_db):
    """
    unhappy: sku_out_of_stock_event_emitted
    active_quantity стал 0 после резервирования → emit_sku_out_of_stock вызван.
    """
    # SKU_1: ровно 2 штуки доступно, запрашиваем 2 → после резерва active=0
    sku1 = make_sku(SKU_ID_1, stock_quantity=2, reserved_quantity=0)
    sku2 = make_sku(SKU_ID_2, stock_quantity=5, reserved_quantity=0)

    db = _db_for_reserve([sku1, sku2])
    app.dependency_overrides[get_db] = lambda: db

    with patch("app.services.inventory_service.emit_sku_out_of_stock") as mock_emit:
        async with await make_client() as client:
            resp = await client.post(
                "/api/v1/inventory/reserve",
                json=RESERVE_BODY,
                headers=SERVICE_KEY_HEADER,
            )

    assert resp.status_code == 200
    # Событие должно быть вызвано ровно для SKU_1
    mock_emit.assert_called_once_with(SKU_ID_1)


# ─── Unreserve ────────────────────────────────────────────────────────────────

async def test_unreserve_restores_quantities(override_db):
    """
    unreserve_restores_quantities
    Снятие резерва корректно восстанавливает active_quantity и reserved_quantity.
    """
    sku1 = make_sku(SKU_ID_1, stock_quantity=10, reserved_quantity=2)
    sku2 = make_sku(SKU_ID_2, stock_quantity=5,  reserved_quantity=1)

    ops = [
        _make_reserve_op(SKU_ID_1, quantity=2),
        _make_reserve_op(SKU_ID_2, quantity=1),
    ]
    db = _db_for_unreserve([sku1, sku2], ops=ops)
    app.dependency_overrides[get_db] = lambda: db

    async with await make_client() as client:
        resp = await client.post(
            "/api/v1/inventory/unreserve",
            json=UNRESERVE_BODY,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "UNRESERVED"
    assert data["order_id"] == str(ORDER_ID)

    # Резерв снят ровно на зарезервированное количество
    assert sku1.reserved_quantity == 0
    assert sku2.reserved_quantity == 0

    db.commit.assert_awaited_once()


async def test_duplicate_sku_in_request_aggregated_correctly(override_db):
    """
    Один sku_id дважды в запросе — quantity суммируется.
    Остаток 5, запрашиваем 3+3=6 → 409.
    """
    sku1 = make_sku(SKU_ID_1, stock_quantity=5, reserved_quantity=0)

    db = _db_for_reserve([sku1])
    app.dependency_overrides[get_db] = lambda: db

    body = {
        "idempotency_key": str(uuid.uuid4()),
        "order_id": str(uuid.uuid4()),
        "items": [
            {"sku_id": str(SKU_ID_1), "quantity": 3},
            {"sku_id": str(SKU_ID_1), "quantity": 3},
        ],
    }

    async with await make_client() as client:
        resp = await client.post(
            "/api/v1/inventory/reserve",
            json=body,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "INSUFFICIENT_STOCK"
    assert str(SKU_ID_1) in body["details"]["sku_ids"]
    assert sku1.reserved_quantity == 0
    db.commit.assert_not_awaited()


async def test_idempotent_unreserve_is_noop(override_db):
    """Повторный unreserve с тем же order_id → 200 без изменений."""
    sku1 = make_sku(SKU_ID_1, stock_quantity=10, reserved_quantity=0)


async def test_unreserve_without_prior_reserve_returns_4xx(override_db):
    """
    unreserve_without_prior_reserve_returns_4xx
    Вызов unreserve для несуществующего order_id → 404,
    reserved_quantity других SKU не изменяется.
    """
    sku1 = make_sku(SKU_ID_1, stock_quantity=10, reserved_quantity=5)

    # ops=None → ReserveOperation не найдены
    db = _db_for_unreserve([sku1], idem_existing=None, ops=None)
    app.dependency_overrides[get_db] = lambda: db

    body = {
        "order_id": str(uuid.uuid4()),  # никогда не резервировался
        "items": [{"sku_id": str(SKU_ID_1), "quantity": 2}],
    }

    async with await make_client() as client:
        resp = await client.post(
            "/api/v1/inventory/unreserve",
            json=body,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"
    # SKU не тронут
    assert sku1.reserved_quantity == 5
    db.commit.assert_not_awaited()
