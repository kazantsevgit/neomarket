"""
Тесты B2B → Moderation событий (MOD-1).

DoD-сценарии:
  created_pending — CREATED создаёт карточку в PENDING;
  edited_returns_to_review — EDITED после MODERATED/BLOCKED возвращает карточку в очередь;
  edited_updates_in_review — EDITED во время IN_REVIEW обновляет поля;
  deleted_archived — DELETED уводит карточку из очереди;
  duplicate_event_no_side_effects — повтор (product_id,date) → 200 без изменений;
  missing_service_header_401 — без X-Service-Key → 401.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import Delete

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import Result

from app.main import app
from app.dependencies.db import get_db
from app.models.b2b_event_idempotency import B2BEventIdempotency
from app.models.field_report import FieldReport
from app.models.product_moderation import ModerationStatus, ProductModeration

_NOW = datetime.now(timezone.utc)
PRODUCT_ID = uuid.uuid4()
SELLER_ID = uuid.uuid4()
B2B_TO_MOD_KEY = "dev-b2b-to-mod-key"


def _base_event(event: str) -> dict:
    return {
        "product_id": str(PRODUCT_ID),
        "seller_id": str(SELLER_ID),
        "event": event,
        "date": _NOW.isoformat(),
    }


def _make_product_moderation(
    status: ModerationStatus = ModerationStatus.PENDING,
    queue_priority: int = 1,
    json_after: dict | None = None,
) -> MagicMock:
    pm = MagicMock(spec=ProductModeration)
    pm.id = uuid.uuid4()
    pm.product_id = PRODUCT_ID
    pm.seller_id = SELLER_ID
    pm.json_before = None
    pm.json_after = json_after or {"id": str(PRODUCT_ID), "title": "Test"}
    pm.status = status
    pm.queue_priority = queue_priority
    pm.moderator_id = None
    pm.date_created = _NOW
    pm.date_updated = _NOW
    return pm


def _product_data(active_qty: int = 5) -> dict:
    return {
        "id": str(PRODUCT_ID),
        "title": "Test Product",
        "description": "Desc",
        "seller_id": str(SELLER_ID),
        "skus": [
            {"stock_quantity": 10, "reserved_quantity": 5},
        ],
    }


@pytest.fixture(autouse=True)
def override_service_key(monkeypatch):
    monkeypatch.setattr("app.config.settings.B2B_TO_MOD_KEY", B2B_TO_MOD_KEY)
    monkeypatch.setattr("app.routers.b2b_events.settings.B2B_TO_MOD_KEY", B2B_TO_MOD_KEY)


@pytest.fixture(autouse=True)
def override_db():
    fake_db = AsyncMock()
    fake_db.commit = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def patch_fetch_product():
    with patch("app.services.b2b_event_service.fetch_product_from_b2b") as mock:
        mock.return_value = _product_data()
        yield mock


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _configure_db_for_created(
    db: AsyncMock,
    idem_record: MagicMock | None = None,
) -> None:
    """Настраивает моки для CREATED: нет idempotency + нет существующего тикета."""
    db.get.side_effect = lambda model, pk: idem_record if model == B2BEventIdempotency else None

    result_mock = MagicMock(spec=Result)
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock


# ─── Happy path: CREATED ──────────────────────────────────────────────────


async def test_created_pending(override_db):
    """
    happy: created_pending
    CREATED → создаёт ProductModeration в статусе PENDING.
    """
    db = override_db
    _configure_db_for_created(db, idem_record=None)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("CREATED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    added_args = [c for c in db.add.call_args_list if c[0][0].__class__.__name__ == "ProductModeration"]
    assert len(added_args) == 1
    ticket = added_args[0][0][0]
    assert ticket.product_id == PRODUCT_ID
    assert ticket.seller_id == SELLER_ID
    assert ticket.status == ModerationStatus.PENDING
    assert ticket.queue_priority == 1
    assert ticket.json_before is None

    db.commit.assert_awaited_once()


async def test_created_duplicate_400(override_db):
    """
    unhappy: created_duplicate_400
    CREATED когда уже есть тикет → 400 Bad Request.
    """
    db = override_db
    existing_ticket = _make_product_moderation(
        status=ModerationStatus.PENDING
    )

    db.get.side_effect = lambda model, pk: None if model == B2BEventIdempotency else None

    result_mock = MagicMock(spec=Result)
    result_mock.scalar_one_or_none.return_value = existing_ticket
    db.execute.return_value = result_mock

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("CREATED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 400
    assert resp.json()["code"] == "DUPLICATE_CREATED"


async def test_created_hard_blocked_ignored(override_db):
    """
    cascade: created_hard_blocked_ignored
    CREATED когда тикет в HARD_BLOCKED → 200, игнорируем.
    """
    db = override_db
    existing_ticket = _make_product_moderation(
        status=ModerationStatus.HARD_BLOCKED
    )

    db.get.side_effect = lambda model, pk: None if model == B2BEventIdempotency else None

    result_mock = MagicMock(spec=Result)
    result_mock.scalar_one_or_none.return_value = existing_ticket
    db.execute.return_value = result_mock

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("CREATED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 200
    # B2BEventIdempotency should be saved, but no ProductModeration added
    added_pm = [c for c in db.add.call_args_list if c[0][0].__class__.__name__ == "ProductModeration"]
    assert len(added_pm) == 0


# ─── Happy path: EDITED ───────────────────────────────────────────────────


def _configure_db_for_edited(
    db: AsyncMock,
    existing_ticket: MagicMock,
    idem_record: MagicMock | None = None,
) -> None:
    db.get.side_effect = lambda model, pk: idem_record if model == B2BEventIdempotency else None

    result_mock = MagicMock(spec=Result)
    result_mock.scalar_one_or_none.return_value = existing_ticket
    db.execute.return_value = result_mock


async def test_edited_after_blocked_priority2(override_db):
    """
    happy: edited_returns_to_review (BLOCKED)
    EDITED после BLOCKED → queue_priority=2, статус PENDING.
    """
    db = override_db
    ticket = _make_product_moderation(
        status=ModerationStatus.BLOCKED,
        json_after=_product_data(),
    )
    _configure_db_for_edited(db, ticket)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("EDITED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 200
    assert ticket.status == ModerationStatus.PENDING
    assert ticket.queue_priority == 2
    assert ticket.moderator_id is None
    assert ticket.json_before is not None
    db.commit.assert_awaited_once()


async def test_edited_after_moderated_with_stock_priority3(override_db, patch_fetch_product):
    """
    happy: edited_returns_to_review (MODERATED + stock)
    EDITED после MODERATED, total_active_quantity>0 → queue_priority=3.
    """
    patch_fetch_product.return_value = _product_data(active_qty=10)  # stock=10, reserved=5 → active=5

    db = override_db
    ticket = _make_product_moderation(
        status=ModerationStatus.MODERATED,
        json_after=_product_data(),
    )
    _configure_db_for_edited(db, ticket)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("EDITED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 200
    assert ticket.status == ModerationStatus.PENDING
    assert ticket.queue_priority == 3


async def test_edited_after_moderated_no_stock_priority4(override_db, patch_fetch_product):
    """
    happy: edited_returns_to_review (MODERATED + no stock)
    EDITED после MODERATED, total_active_quantity=0 → queue_priority=4.
    """
    patch_fetch_product.return_value = {
        "id": str(PRODUCT_ID),
        "title": "Test Product",
        "seller_id": str(SELLER_ID),
        "skus": [
            {"stock_quantity": 5, "reserved_quantity": 5},
        ],
    }

    db = override_db
    ticket = _make_product_moderation(
        status=ModerationStatus.MODERATED,
        json_after=_product_data(),
    )
    _configure_db_for_edited(db, ticket)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("EDITED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 200
    assert ticket.status == ModerationStatus.PENDING
    assert ticket.queue_priority == 4


async def test_edited_updates_fields(override_db):
    """
    happy: edited_updates_in_review
    EDITED во время IN_REVIEW обновляет json_before, json_after,
    сбрасывает статус в PENDING и moderator_id в null.
    """
    db = override_db
    old_json = {"id": str(PRODUCT_ID), "title": "Old Title"}
    ticket = _make_product_moderation(
        status=ModerationStatus.IN_REVIEW,
        json_after=old_json,
    )
    ticket.moderator_id = uuid.uuid4()
    _configure_db_for_edited(db, ticket)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("EDITED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 200
    assert ticket.json_before == old_json
    assert ticket.json_after == _product_data()
    assert ticket.status == ModerationStatus.PENDING
    assert ticket.moderator_id is None
    # Приоритет остаётся текущим для PENDING/IN_REVIEW
    assert ticket.queue_priority == 1


async def test_edited_hard_blocked_ignored(override_db):
    """
    cascade: edited_hard_blocked_ignored
    EDITED когда HARD_BLOCKED → 200, без изменений.
    """
    db = override_db
    ticket = _make_product_moderation(status=ModerationStatus.HARD_BLOCKED)
    _configure_db_for_edited(db, ticket)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("EDITED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 200
    # Статус не изменился
    assert ticket.status == ModerationStatus.HARD_BLOCKED
    # json_before не обновлялся
    assert ticket.json_before is None


async def test_edited_no_ticket_400(override_db):
    """
    unhappy: edited_no_ticket_400
    EDITED без существующего тикета → 400.
    """
    db = override_db
    db.get.side_effect = lambda model, pk: None if model == B2BEventIdempotency else None

    result_mock = MagicMock(spec=Result)
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("EDITED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 400
    assert resp.json()["code"] == "TICKET_NOT_FOUND"


# ─── Happy path: DELETED ──────────────────────────────────────────────────


async def test_deleted_archived(override_db):
    """
    happy: deleted_archived
    DELETED удаляет тикет из БД.
    """
    db = override_db
    ticket = _make_product_moderation(status=ModerationStatus.PENDING)

    db.get.side_effect = lambda model, pk: None if model == B2BEventIdempotency else None

    result_mock = MagicMock(spec=Result)
    result_mock.scalar_one_or_none.return_value = ticket
    db.execute.return_value = result_mock

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("DELETED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 200
    db.delete.assert_awaited_once_with(ticket)
    db.commit.assert_awaited_once()


async def test_deleted_no_ticket_idempotent(override_db):
    """
    cascade: deleted_no_ticket_idempotent
    DELETED без тикета → 200 OK (идемпотентно).
    """
    db = override_db
    db.get.side_effect = lambda model, pk: None if model == B2BEventIdempotency else None

    result_mock = MagicMock(spec=Result)
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("DELETED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 200
    db.delete.assert_not_awaited()
    db.commit.assert_awaited_once()  # idempotency record


# ─── Idempotency ──────────────────────────────────────────────────────────


async def test_duplicate_event_no_side_effects(override_db):
    """
    unhappy: duplicate_event_no_side_effects
    Повтор с той же парой (product_id, date) → 200, никаких изменений.
    """
    db = override_db
    idem_record = MagicMock(spec=B2BEventIdempotency)
    idem_record.product_id = PRODUCT_ID
    idem_record.event_date = _NOW
    idem_record.event_type = "CREATED"

    db.get.side_effect = lambda model, pk: (
        idem_record if model == B2BEventIdempotency else None
    )

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("CREATED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    # Никаких add для ProductModeration
    added_pm = [c for c in db.add.call_args_list if c[0][0].__class__.__name__ == "ProductModeration"]
    assert len(added_pm) == 0
    db.commit.assert_not_awaited()


async def test_duplicate_event_diff_date_not_ignored(override_db):
    """
    cascade: duplicate_event_diff_date_not_ignored
    То же product_id, другая дата → обрабатывается, не дубль.
    """
    db = override_db
    earlier_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    later_date = datetime(2026, 6, 1, tzinfo=timezone.utc)

    idem_record = MagicMock(spec=B2BEventIdempotency)
    idem_record.product_id = PRODUCT_ID
    idem_record.event_date = earlier_date
    idem_record.event_type = "CREATED"

    def get_side(model, pk):
        if model == B2BEventIdempotency and isinstance(pk, tuple) and pk[0] == PRODUCT_ID and pk[1] == later_date:
            return None
        if model == B2BEventIdempotency:
            return idem_record
        return None

    db.get.side_effect = get_side

    result_mock = MagicMock(spec=Result)
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock

    body = _base_event("CREATED")
    body["date"] = later_date.isoformat()

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=body,
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 200
    added_pm = [c for c in db.add.call_args_list if c[0][0].__class__.__name__ == "ProductModeration"]
    assert len(added_pm) == 1


# ─── Auth ─────────────────────────────────────────────────────────────────


async def test_missing_service_header_401(override_db):
    """
    unhappy: missing_service_header_401
    Запрос без X-Service-Key → 401.
    """
    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("CREATED"),
        )

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_wrong_service_key_401(override_db):
    """
    unhappy: wrong_service_key_401
    Неправильный X-Service-Key → 401.
    """
    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("CREATED"),
            headers={"X-Service-Key": "wrong-key"},
        )

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


# ─── B2B unavailable ──────────────────────────────────────────────────────


async def test_b2b_unavailable_500(override_db, patch_fetch_product):
    """
    unhappy: b2b_unavailable_500
    B2B недоступен при CREATED → 500.
    """
    from app.services.b2b_client import B2BUnavailableError
    patch_fetch_product.side_effect = B2BUnavailableError("B2B down")

    db = override_db
    _configure_db_for_created(db)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("CREATED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 500
    db.commit.assert_not_awaited()


# ─── Field report cleanup ─────────────────────────────────────────────────


async def test_edited_clears_field_reports(override_db):
    """
    cascade: edited_clears_field_reports
    EDITED удаляет field_reports для данного тикета.
    """
    db = override_db
    ticket = _make_product_moderation(status=ModerationStatus.BLOCKED)
    _configure_db_for_edited(db, ticket)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("EDITED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 200

    delete_calls = [
        c for c in db.execute.call_args_list
        if c[0] and isinstance(c[0][0], Delete)
    ]
    assert len(delete_calls) >= 1


# ─── EDITED keeps existing priority for PENDING/IN_REVIEW ─────────────────


async def test_edited_keeps_priority_for_in_review(override_db):
    """
    cascade: edited_keeps_priority_for_in_review
    EDITED во время IN_REVIEW сохраняет текущий queue_priority.
    """
    db = override_db
    ticket = _make_product_moderation(
        status=ModerationStatus.IN_REVIEW,
        queue_priority=1,
    )
    _configure_db_for_edited(db, ticket)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/events/product",
            json=_base_event("EDITED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 200
    assert ticket.queue_priority == 1
