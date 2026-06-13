"""
Тесты B2B → Moderation событий (MOD-1).

DoD-сценарии:
  created_pending — PRODUCT_CREATED создаёт карточку в PENDING;
  edited_returns_to_review — PRODUCT_EDITED после MODERATED/BLOCKED возвращает карточку в очередь;
  edited_updates_in_review — PRODUCT_EDITED во время IN_REVIEW обновляет поля;
  deleted_archived — PRODUCT_DELETED уводит карточку из очереди;
  duplicate_event_no_side_effects — повтор idempotency_key → 202 без изменений;
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
IDEM_KEY = uuid.uuid4()
B2B_TO_MOD_KEY = "dev-b2b-to-mod-key"
PRIORITY_DEFAULT = 3


def _base_event(event_type: str, payload_override: dict | None = None) -> dict:
    payload = payload_override or {
        "product_id": str(PRODUCT_ID),
        "seller_id": str(SELLER_ID),
        "json_after": {"id": str(PRODUCT_ID), "title": "Test"},
    }
    return {
        "event_type": event_type,
        "idempotency_key": str(IDEM_KEY),
        "occurred_at": _NOW.isoformat(),
        "payload": payload,
    }


def _deleted_event() -> dict:
    return {
        "event_type": "PRODUCT_DELETED",
        "idempotency_key": str(IDEM_KEY),
        "occurred_at": _NOW.isoformat(),
        "payload": {"product_id": str(PRODUCT_ID)},
    }


def _make_product_moderation(
    status: ModerationStatus = ModerationStatus.PENDING,
    queue_priority: int = PRIORITY_DEFAULT,
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


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _configure_db_for_created(
    db: AsyncMock,
    idem_record: MagicMock | None = None,
) -> None:
    db.get.side_effect = lambda model, pk: idem_record if model == B2BEventIdempotency else None

    result_mock = MagicMock(spec=Result)
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock


# ─── Happy path: PRODUCT_CREATED ───────────────────────────────────────────


async def test_created_pending(override_db):
    db = override_db
    _configure_db_for_created(db, idem_record=None)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=_base_event("PRODUCT_CREATED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 202

    added_args = [c for c in db.add.call_args_list if c[0][0].__class__.__name__ == "ProductModeration"]
    assert len(added_args) == 1
    ticket = added_args[0][0][0]
    assert ticket.product_id == PRODUCT_ID
    assert ticket.seller_id == SELLER_ID
    assert ticket.status == ModerationStatus.PENDING
    assert ticket.queue_priority == PRIORITY_DEFAULT
    assert ticket.json_before is None

    db.commit.assert_awaited_once()


async def test_created_duplicate_400(override_db):
    db = override_db
    existing_ticket = _make_product_moderation(status=ModerationStatus.PENDING)

    db.get.side_effect = lambda model, pk: None if model == B2BEventIdempotency else None

    result_mock = MagicMock(spec=Result)
    result_mock.scalar_one_or_none.return_value = existing_ticket
    db.execute.return_value = result_mock

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=_base_event("PRODUCT_CREATED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 400
    assert resp.json()["code"] == "DUPLICATE_CREATED"


async def test_created_hard_blocked_ignored(override_db):
    db = override_db
    existing_ticket = _make_product_moderation(status=ModerationStatus.HARD_BLOCKED)

    db.get.side_effect = lambda model, pk: None if model == B2BEventIdempotency else None

    result_mock = MagicMock(spec=Result)
    result_mock.scalar_one_or_none.return_value = existing_ticket
    db.execute.return_value = result_mock

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=_base_event("PRODUCT_CREATED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 202

    added_pm = [c for c in db.add.call_args_list if c[0][0].__class__.__name__ == "ProductModeration"]
    assert len(added_pm) == 0


# ─── Happy path: PRODUCT_EDITED ────────────────────────────────────────────


def _edited_payload() -> dict:
    return {
        "product_id": str(PRODUCT_ID),
        "seller_id": str(SELLER_ID),
        "json_before": {"id": str(PRODUCT_ID), "title": "Old Title"},
        "json_after": {"id": str(PRODUCT_ID), "title": "New Title"},
    }


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
    db = override_db
    ticket = _make_product_moderation(
        status=ModerationStatus.BLOCKED,
        json_after={"id": str(PRODUCT_ID), "title": "Old"},
    )
    _configure_db_for_edited(db, ticket)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=_base_event("PRODUCT_EDITED", payload_override=_edited_payload()),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 202
    assert ticket.status == ModerationStatus.PENDING
    assert ticket.queue_priority == 2
    assert ticket.moderator_id is None
    assert ticket.json_before is not None
    db.commit.assert_awaited_once()


async def test_edited_after_moderated_priority3(override_db):
    db = override_db
    ticket = _make_product_moderation(
        status=ModerationStatus.MODERATED,
        json_after={"id": str(PRODUCT_ID), "title": "Old"},
    )
    _configure_db_for_edited(db, ticket)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=_base_event("PRODUCT_EDITED", payload_override={
                **_edited_payload(),
                "queue_priority": 3,
            }),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 202
    assert ticket.status == ModerationStatus.PENDING
    assert ticket.queue_priority == 3


async def test_edited_after_moderated_priority4(override_db):
    db = override_db
    ticket = _make_product_moderation(
        status=ModerationStatus.MODERATED,
        json_after={"id": str(PRODUCT_ID), "title": "Old"},
    )
    _configure_db_for_edited(db, ticket)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=_base_event("PRODUCT_EDITED", payload_override={
                **_edited_payload(),
                "queue_priority": 4,
            }),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 202
    assert ticket.status == ModerationStatus.PENDING
    assert ticket.queue_priority == 4


async def test_edited_updates_fields(override_db):
    db = override_db
    old_json = {"id": str(PRODUCT_ID), "title": "Old Title"}
    new_json = {"id": str(PRODUCT_ID), "title": "New Title"}
    ticket = _make_product_moderation(
        status=ModerationStatus.IN_REVIEW,
        json_after=old_json,
    )
    ticket.moderator_id = uuid.uuid4()
    _configure_db_for_edited(db, ticket)

    payload = {
        "product_id": str(PRODUCT_ID),
        "seller_id": str(SELLER_ID),
        "json_before": old_json,
        "json_after": new_json,
    }

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=_base_event("PRODUCT_EDITED", payload_override=payload),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 202
    assert ticket.json_before == old_json
    assert ticket.json_after == new_json
    assert ticket.status == ModerationStatus.PENDING
    assert ticket.moderator_id is None
    assert ticket.queue_priority == PRIORITY_DEFAULT


async def test_edited_hard_blocked_ignored(override_db):
    db = override_db
    ticket = _make_product_moderation(status=ModerationStatus.HARD_BLOCKED)
    _configure_db_for_edited(db, ticket)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=_base_event("PRODUCT_EDITED", payload_override=_edited_payload()),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 202
    assert ticket.status == ModerationStatus.HARD_BLOCKED
    assert ticket.json_before is None


async def test_edited_no_ticket_400(override_db):
    db = override_db
    db.get.side_effect = lambda model, pk: None if model == B2BEventIdempotency else None

    result_mock = MagicMock(spec=Result)
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=_base_event("PRODUCT_EDITED", payload_override=_edited_payload()),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 400
    assert resp.json()["code"] == "TICKET_NOT_FOUND"


# ─── Happy path: PRODUCT_DELETED ───────────────────────────────────────────


async def test_deleted_archived(override_db):
    db = override_db
    ticket = _make_product_moderation(status=ModerationStatus.PENDING)

    db.get.side_effect = lambda model, pk: None if model == B2BEventIdempotency else None

    result_mock = MagicMock(spec=Result)
    result_mock.scalar_one_or_none.return_value = ticket
    db.execute.return_value = result_mock

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=_deleted_event(),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 202
    db.delete.assert_awaited_once_with(ticket)
    db.commit.assert_awaited_once()


async def test_deleted_no_ticket_idempotent(override_db):
    db = override_db
    db.get.side_effect = lambda model, pk: None if model == B2BEventIdempotency else None

    result_mock = MagicMock(spec=Result)
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=_deleted_event(),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 202
    db.delete.assert_not_awaited()
    db.commit.assert_awaited_once()


# ─── Idempotency ───────────────────────────────────────────────────────────


async def test_duplicate_event_no_side_effects(override_db):
    db = override_db
    idem_record = MagicMock(spec=B2BEventIdempotency)
    idem_record.idempotency_key = IDEM_KEY
    idem_record.event_type = "PRODUCT_CREATED"

    db.get.side_effect = lambda model, pk: (
        idem_record if model == B2BEventIdempotency else None
    )

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=_base_event("PRODUCT_CREATED"),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 202

    added_pm = [c for c in db.add.call_args_list if c[0][0].__class__.__name__ == "ProductModeration"]
    assert len(added_pm) == 0
    db.commit.assert_not_awaited()


async def test_duplicate_event_diff_key_not_ignored(override_db):
    db = override_db
    other_key = uuid.uuid4()

    def get_side(model, pk):
        if model == B2BEventIdempotency and pk == other_key:
            return None
        if model == B2BEventIdempotency:
            return MagicMock(spec=B2BEventIdempotency)
        return None

    db.get.side_effect = get_side

    result_mock = MagicMock(spec=Result)
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock

    body = _base_event("PRODUCT_CREATED")
    body["idempotency_key"] = str(other_key)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=body,
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 202
    added_pm = [c for c in db.add.call_args_list if c[0][0].__class__.__name__ == "ProductModeration"]
    assert len(added_pm) == 1


# ─── Auth ──────────────────────────────────────────────────────────────────


async def test_missing_service_header_401(override_db):
    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=_base_event("PRODUCT_CREATED"),
        )

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_wrong_service_key_401(override_db):
    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=_base_event("PRODUCT_CREATED"),
            headers={"X-Service-Key": "wrong-key"},
        )

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


# ─── Field report cleanup ──────────────────────────────────────────────────


async def test_edited_clears_field_reports(override_db):
    db = override_db
    ticket = _make_product_moderation(status=ModerationStatus.BLOCKED)
    _configure_db_for_edited(db, ticket)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=_base_event("PRODUCT_EDITED", payload_override=_edited_payload()),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 202

    delete_calls = [
        c for c in db.execute.call_args_list
        if c[0] and isinstance(c[0][0], Delete)
    ]
    assert len(delete_calls) >= 1


# ─── PRODUCT_EDITED keeps existing priority for PENDING/IN_REVIEW ──────────


async def test_edited_keeps_priority_for_in_review(override_db):
    db = override_db
    ticket = _make_product_moderation(
        status=ModerationStatus.IN_REVIEW,
        queue_priority=1,
    )
    _configure_db_for_edited(db, ticket)

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/b2b/events",
            json=_base_event("PRODUCT_EDITED", payload_override=_edited_payload()),
            headers={"X-Service-Key": B2B_TO_MOD_KEY},
        )

    assert resp.status_code == 202
    assert ticket.queue_priority == 1
