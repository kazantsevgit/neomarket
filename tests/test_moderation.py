"""
Тесты moderation: events + decline (US-MOD-05).

DoD-сценарии:
  moderation events:
    happy:
      - moderated_event_clears_blocking_data
      - blocked_soft_saves_field_reports
      - blocked_hard_sets_terminal_status
    unhappy:
      - duplicate_event_same_idempotency_key_no_side_effects
      - missing_service_key_returns_401
    additional:
      - hard_blocked_product_rejects_seller_edits (интеграционный тест с PUT/DELETE)

  decline (US-MOD-05):
    happy:
      - decline_hard_block_returns_200_and_sets_HARD_BLOCKED
    unhappy:
      - decline_missing_service_key_returns_401
      - decline_product_not_found_returns_404
      - decline_reason_not_found_returns_404
      - decline_reason_not_hard_returns_400
      - decline_already_hard_blocked_returns_409
    cascade:
      - decline_triggers_product_blocked_to_b2c
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.dependencies.db import get_db
from app.models.blocking_reason import BlockingReason
from app.models.moderation_event import ModerationEventIdempotency
from app.models.product import Product, ProductStatus
from app.models.ticket import Ticket, TicketKind, TicketStatus

# ─── Константы ───────────────────────────────────────────────────────────────

_NOW            = datetime.now(timezone.utc)
PRODUCT_ID      = uuid.uuid4()
TICKET_ID       = uuid.uuid4()
IDEM_KEY        = uuid.uuid4()
BLOCKING_REASON = uuid.uuid4()
MODERATOR_ID    = uuid.uuid4()
HARD_REASON_ID  = uuid.uuid4()
SELLER_ID       = uuid.uuid4()

SERVICE_KEY_HEADER = {"X-Service-Key": "test-moderation-key"}

BASE_EVENT = {
    "idempotency_key": str(IDEM_KEY),
    "product_id": str(PRODUCT_ID),
    "occurred_at": _NOW.isoformat(),
}

# ─── Фабрики ─────────────────────────────────────────────────────────────────


def make_product(
    status: ProductStatus = ProductStatus.ON_MODERATION,
    blocking_reason_id: uuid.UUID | None = None,
    field_reports: list | None = None,
) -> MagicMock:
    p = MagicMock(spec=Product)
    p.id                = PRODUCT_ID
    p.status            = status
    p.blocking_reason_id = blocking_reason_id
    p.blocking_reason   = None
    p.moderator_comment = None
    p.field_reports     = field_reports or []
    return p


def make_idempotency_record(event_type: str) -> MagicMock:
    r = MagicMock(spec=ModerationEventIdempotency)
    r.idempotency_key = IDEM_KEY
    r.product_id      = PRODUCT_ID
    r.event_type      = event_type
    return r


def make_ticket(
    status: TicketStatus = TicketStatus.IN_REVIEW,
    product_id: uuid.UUID = PRODUCT_ID,
) -> MagicMock:
    t = MagicMock(spec=Ticket)
    t.id                = TICKET_ID
    t.product_id        = product_id
    t.seller_id         = SELLER_ID
    t.category_id       = None
    t.kind              = TicketKind.CREATE
    t.status            = status
    t.assigned_moderator_id = None
    t.moderator_comment = None
    t.blocking_reason_id = None
    t.edit_pending      = False
    t.decision_at       = None
    t.created_at        = _NOW
    t.updated_at        = _NOW
    t.field_reports     = []
    return t


# ─── Фикстуры ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def override_service_key(monkeypatch):
    """Подменяем MODERATION_SERVICE_KEY в settings."""
    monkeypatch.setattr("app.config.settings.MODERATION_SERVICE_KEY", "test-moderation-key")
    monkeypatch.setattr("app.routers.moderation.settings.MODERATION_SERVICE_KEY", "test-moderation-key")


@pytest.fixture(autouse=True)
def override_db():
    fake_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


async def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _db_for_event(product: MagicMock, idempotency_record=None) -> AsyncMock:
    """
    Настраивает fake_db:
    - get(ModerationEventIdempotency) → idempotency_record
    - get(Product) → product
    """
    db = AsyncMock()

    def get_side_effect(model_class, pk):
        if model_class == ModerationEventIdempotency:
            return idempotency_record
        if model_class == Product:
            return product
        return None

    db.get.side_effect = get_side_effect
    return db


# ─── Happy path ───────────────────────────────────────────────────────────────


async def test_moderated_event_clears_blocking_data(override_db):
    """
    happy: moderated_event_clears_blocking_data
    MODERATED → status=MODERATED, blocking_reason и field_reports очищены.
    """
    product = make_product(
        status=ProductStatus.ON_MODERATION,
        blocking_reason_id=BLOCKING_REASON,
        field_reports=[{"field_name": "title", "comment": "Fix this"}],
    )

    db = _db_for_event(product)
    app.dependency_overrides[get_db] = lambda: db

    event = {
        **BASE_EVENT,
        "event_type": "MODERATED",
        "moderator_comment": "Looks good",
    }

    async with await make_client() as client:
        resp = await client.post(
            "/api/v1/moderation/events",
            json=event,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 204
    assert product.status == ProductStatus.MODERATED
    assert product.blocking_reason_id is None
    assert product.field_reports == []
    assert product.moderator_comment == "Looks good"
    db.commit.assert_awaited_once()


async def test_blocked_soft_saves_field_reports(override_db):
    """
    happy: blocked_soft_saves_field_reports
    BLOCKED + hard_block=false → BLOCKED, field_reports сохранены, каскад в B2C.
    """
    product = make_product(status=ProductStatus.ON_MODERATION)

    db = _db_for_event(product)
    app.dependency_overrides[get_db] = lambda: db

    event = {
        **BASE_EVENT,
        "event_type": "BLOCKED",
        "hard_block": False,
        "blocking_reason_id": str(BLOCKING_REASON),
        "moderator_comment": "Title is misleading",
        "field_reports": [
            {"field_name": "title", "comment": "Must not contain 'original'"},
            {"field_name": "description", "comment": "Too short"},
        ],
    }

    with patch("app.services.moderation_service.emit_product_blocked_to_b2b") as mock_emit:
        async with await make_client() as client:
            resp = await client.post(
                "/api/v1/moderation/events",
                json=event,
                headers=SERVICE_KEY_HEADER,
            )

    assert resp.status_code == 204
    assert product.status == ProductStatus.BLOCKED
    assert product.blocking_reason_id == BLOCKING_REASON
    assert len(product.field_reports) == 2
    assert product.field_reports[0]["field_name"] == "title"
    assert product.field_reports[1]["field_name"] == "description"

    # Каскадное событие в B2B
    mock_emit.assert_called_once_with(PRODUCT_ID, hard_block=False)
    db.commit.assert_awaited_once()


async def test_blocked_hard_sets_terminal_status(override_db):
    """
    happy: blocked_hard_sets_terminal_status
    BLOCKED + hard_block=true → HARD_BLOCKED, каскад в B2B.
    """
    product = make_product(status=ProductStatus.ON_MODERATION)

    db = _db_for_event(product)
    app.dependency_overrides[get_db] = lambda: db

    event = {
        **BASE_EVENT,
        "event_type": "BLOCKED",
        "hard_block": True,
        "blocking_reason_id": str(BLOCKING_REASON),
        "moderator_comment": "Counterfeit detected",
    }

    with patch("app.services.moderation_service.emit_product_blocked_to_b2b") as mock_emit:
        async with await make_client() as client:
            resp = await client.post(
                "/api/v1/moderation/events",
                json=event,
                headers=SERVICE_KEY_HEADER,
            )

    assert resp.status_code == 204
    assert product.status == ProductStatus.HARD_BLOCKED
    assert product.blocking_reason_id == BLOCKING_REASON

    mock_emit.assert_called_once_with(PRODUCT_ID, hard_block=True)
    db.commit.assert_awaited_once()


# ─── Unhappy path ─────────────────────────────────────────────────────────────


async def test_duplicate_event_same_idempotency_key_no_side_effects(override_db):
    """
    unhappy: duplicate_event_same_idempotency_key_no_side_effects
    Повторное событие с тем же idempotency_key → 204, товар не изменён.
    """
    product = make_product(status=ProductStatus.MODERATED)
    idem_record = make_idempotency_record("MODERATED")

    db = _db_for_event(product, idempotency_record=idem_record)
    app.dependency_overrides[get_db] = lambda: db

    event = {
        **BASE_EVENT,
        "event_type": "MODERATED",
    }

    async with await make_client() as client:
        resp = await client.post(
            "/api/v1/moderation/events",
            json=event,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 204
    # Товар не изменён
    assert product.status == ProductStatus.MODERATED
    # commit не вызывался — дедупликация сработала
    db.commit.assert_not_awaited()


async def test_missing_service_key_returns_401(override_db):
    """
    unhappy: missing_service_key_returns_401
    Запрос без X-Service-Key → 401.
    """
    event = {
        **BASE_EVENT,
        "event_type": "MODERATED",
    }

    async with await make_client() as client:
        resp = await client.post(
            "/api/v1/moderation/events",
            json=event,
            # Без SERVICE_KEY_HEADER
        )

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_wrong_service_key_returns_401(override_db):
    """
    unhappy: неправильный X-Service-Key → 401.
    """
    event = {
        **BASE_EVENT,
        "event_type": "MODERATED",
    }

    async with await make_client() as client:
        resp = await client.post(
            "/api/v1/moderation/events",
            json=event,
            headers={"X-Service-Key": "wrong-key"},
        )

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


# ─── Additional test: HARD_BLOCKED защита ────────────────────────────────────
# Этот тест требует реальной логики в product update/delete endpoints
# Пока оставляю как документацию требования DoD


async def test_hard_blocked_product_rejects_seller_edits_put(override_db):
    """PUT на HARD_BLOCKED товар → 403."""
    from app.dependencies.auth import get_current_seller_id
    SELLER_ID = uuid.uuid4()
    app.dependency_overrides[get_current_seller_id] = lambda: SELLER_ID

    product = make_product(status=ProductStatus.HARD_BLOCKED)
    product.seller_id = SELLER_ID

    db = AsyncMock()
    db.get.return_value = product
    app.dependency_overrides[get_db] = lambda: db

    update_body = {
        "title": "New title",
        "description": "New description",
        "category_id": str(uuid.uuid4()),
        "characteristics": [],
        "images": [{"url": "https://cdn.example.com/img.jpg", "ordering": 0}],
    }

    async with await make_client() as client:
        resp = await client.put(
            f"/api/v1/products/{PRODUCT_ID}",
            json=update_body,
            headers={"Authorization": "Bearer token"},
        )

    app.dependency_overrides.pop(get_current_seller_id, None)
    assert resp.status_code == 403


async def test_hard_blocked_product_rejects_seller_edits_delete(override_db):
    """DELETE на HARD_BLOCKED товар → 403."""
    from app.dependencies.auth import get_current_seller_id
    SELLER_ID = uuid.uuid4()
    app.dependency_overrides[get_current_seller_id] = lambda: SELLER_ID

    product = make_product(status=ProductStatus.HARD_BLOCKED)
    product.seller_id = SELLER_ID

    db = AsyncMock()
    db.get.return_value = product
    app.dependency_overrides[get_db] = lambda: db

    async with await make_client() as client:
        resp = await client.delete(
            f"/api/v1/products/{PRODUCT_ID}",
            headers={"Authorization": "Bearer token"},
        )

    app.dependency_overrides.pop(get_current_seller_id, None)
    assert resp.status_code == 403


async def test_hard_blocked_product_ignores_new_moderation_event(override_db):
    """HARD_BLOCKED товар игнорирует новые события модерации с другим ключом."""
    product = make_product(status=ProductStatus.HARD_BLOCKED)

    db = _db_for_event(product, idempotency_record=None)
    app.dependency_overrides[get_db] = lambda: db

    event = {
        "idempotency_key": str(uuid.uuid4()),  # новый ключ — не дубль
        "product_id": str(PRODUCT_ID),
        "occurred_at": _NOW.isoformat(),
        "event_type": "MODERATED",
        "moderator_comment": "Attempt to unblock",
    }

    async with await make_client() as client:
        resp = await client.post(
            "/api/v1/moderation/events",
            json=event,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 204
    # Статус не изменился
    assert product.status == ProductStatus.HARD_BLOCKED


# ─── Фабрика BlockingReason ──────────────────────────────────────────────────


def make_reason(
    hard_block: bool = True,
) -> MagicMock:
    r = MagicMock(spec=BlockingReason)
    r.id         = HARD_REASON_ID
    r.code       = "COUNTERFEIT"
    r.title      = "Контрафактный товар"
    r.description = None
    r.hard_block  = hard_block
    r.is_active   = True
    return r


# ─── Block tests (US-MOD-04/05 via tickets) ────────────────────────────────────

_BLOCK_URL = f"/api/v1/tickets/{TICKET_ID}/block"

BLOCK_BODY = {
    "blocking_reason_ids": [str(HARD_REASON_ID)],
    "comment": "Товар является контрафактом, подтверждено проверкой",
    "field_reports": [],
}


def _db_for_block(
    ticket: MagicMock,
    product: MagicMock | None = None,
    reason: MagicMock | None = None,
) -> AsyncMock:
    """Настраивает fake_db для block: get(Ticket), get(Product), get(BlockingReason)."""
    db = AsyncMock()

    def get_side_effect(model_class, pk):
        if model_class == Ticket:
            return ticket
        if model_class == Product:
            return product
        if model_class == BlockingReason:
            return reason
        return None

    db.get.side_effect = get_side_effect
    return db


async def test_block_hard_sets_HARD_BLOCKED_and_returns_TicketResponse(override_db):
    """
    happy: block с hard_block причиной → 200, статус HARD_BLOCKED, ответ TicketResponse.
    """
    ticket  = make_ticket(status=TicketStatus.IN_REVIEW)
    product = make_product(status=ProductStatus.ON_MODERATION)
    reason  = make_reason(hard_block=True)

    db = _db_for_block(ticket, product=product, reason=reason)
    app.dependency_overrides[get_db] = lambda: db

    with patch("app.services.moderation_service.emit_product_blocked_to_b2b") as mock_emit:
        async with await make_client() as client:
            resp = await client.post(
                _BLOCK_URL,
                json=BLOCK_BODY,
                headers=SERVICE_KEY_HEADER,
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(TICKET_ID)
    assert data["product_id"] == str(PRODUCT_ID)
    assert data["seller_id"] == str(SELLER_ID)
    assert data["status"] == "HARD_BLOCKED"
    assert data["kind"] == "CREATE"

    assert product.status == ProductStatus.HARD_BLOCKED
    assert product.blocking_reason_id == HARD_REASON_ID
    assert product.moderator_comment == "Товар является контрафактом, подтверждено проверкой"
    assert ticket.status == TicketStatus.HARD_BLOCKED

    mock_emit.assert_called_once_with(PRODUCT_ID, hard_block=True)
    db.commit.assert_awaited_once()


async def test_block_soft_sets_BLOCKED(override_db):
    """
    happy: block с soft-block причиной → 200, статус BLOCKED.
    """
    ticket  = make_ticket(status=TicketStatus.IN_REVIEW)
    product = make_product(status=ProductStatus.ON_MODERATION)
    reason  = make_reason(hard_block=False)

    db = _db_for_block(ticket, product=product, reason=reason)
    app.dependency_overrides[get_db] = lambda: db

    body = {
        "blocking_reason_ids": [str(HARD_REASON_ID)],
        "comment": "Мягкая блокировка",
        "field_reports": [],
    }

    with patch("app.services.moderation_service.emit_product_blocked_to_b2b") as mock_emit:
        async with await make_client() as client:
            resp = await client.post(
                _BLOCK_URL,
                json=body,
                headers=SERVICE_KEY_HEADER,
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "BLOCKED"
    assert product.status == ProductStatus.BLOCKED
    assert ticket.status == TicketStatus.BLOCKED
    mock_emit.assert_called_once_with(PRODUCT_ID, hard_block=False)


async def test_block_missing_service_key_returns_401(override_db):
    """unhappy: без X-Service-Key → 401."""
    async with await make_client() as client:
        resp = await client.post(
            _BLOCK_URL,
            json=BLOCK_BODY,
        )

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_block_wrong_service_key_returns_401(override_db):
    """unhappy: неверный ключ → 401."""
    async with await make_client() as client:
        resp = await client.post(
            _BLOCK_URL,
            json=BLOCK_BODY,
            headers={"X-Service-Key": "wrong-key"},
        )

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_block_ticket_not_found_returns_404(override_db):
    """unhappy: тикет не найден → 404."""
    db = AsyncMock()
    db.get.side_effect = lambda model, pk: None
    app.dependency_overrides[get_db] = lambda: db

    async with await make_client() as client:
        resp = await client.post(
            _BLOCK_URL,
            json=BLOCK_BODY,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 404


async def test_block_product_not_found_returns_404(override_db):
    """unhappy: товар по тикету не найден → 404."""
    ticket = make_ticket(status=TicketStatus.IN_REVIEW)

    db = AsyncMock()
    db.get.side_effect = lambda model, pk: ticket if model == Ticket else None
    app.dependency_overrides[get_db] = lambda: db

    async with await make_client() as client:
        resp = await client.post(
            _BLOCK_URL,
            json=BLOCK_BODY,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 404


async def test_block_reason_not_found_returns_404(override_db):
    """unhappy: причина блокировки не найдена → 404."""
    ticket  = make_ticket(status=TicketStatus.IN_REVIEW)
    product = make_product(status=ProductStatus.ON_MODERATION)

    db = AsyncMock()
    db.get.side_effect = lambda model, pk: (
        ticket if model == Ticket else
        product if model == Product else
        None
    )
    app.dependency_overrides[get_db] = lambda: db

    async with await make_client() as client:
        resp = await client.post(
            _BLOCK_URL,
            json=BLOCK_BODY,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 404


async def test_block_already_hard_blocked_returns_409(override_db):
    """unhappy: тикет уже HARD_BLOCKED → 409."""
    ticket  = make_ticket(status=TicketStatus.HARD_BLOCKED)
    product = make_product(status=ProductStatus.HARD_BLOCKED)
    reason  = make_reason(hard_block=True)

    db = _db_for_block(ticket, product=product, reason=reason)
    app.dependency_overrides[get_db] = lambda: db

    async with await make_client() as client:
        resp = await client.post(
            _BLOCK_URL,
            json=BLOCK_BODY,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 409
    assert resp.json()["code"] == "ALREADY_HARD_BLOCKED"


async def test_block_wrong_status_returns_409(override_db):
    """unhappy: тикет не в IN_REVIEW → 409 WRONG_STATUS."""
    ticket  = make_ticket(status=TicketStatus.PENDING)
    product = make_product(status=ProductStatus.ON_MODERATION)
    reason  = make_reason(hard_block=True)

    db = _db_for_block(ticket, product=product, reason=reason)
    app.dependency_overrides[get_db] = lambda: db

    async with await make_client() as client:
        resp = await client.post(
            _BLOCK_URL,
            json=BLOCK_BODY,
            headers=SERVICE_KEY_HEADER,
        )

    assert resp.status_code == 409
    assert resp.json()["code"] == "WRONG_STATUS"
    assert "IN_REVIEW" in resp.json()["message"]


async def test_block_with_field_reports(override_db):
    """
    happy: block с field_reports сохраняет их на товаре и тикете.
    """
    ticket  = make_ticket(status=TicketStatus.IN_REVIEW)
    product = make_product(status=ProductStatus.ON_MODERATION)
    reason  = make_reason(hard_block=True)

    db = _db_for_block(ticket, product=product, reason=reason)
    app.dependency_overrides[get_db] = lambda: db

    body = {
        "blocking_reason_ids": [str(HARD_REASON_ID)],
        "comment": "Проблемы с описанием",
        "field_reports": [
            {"field_path": "title", "message": "Не соответствует товару"},
            {"field_path": "description", "message": "Слишком короткое"},
        ],
    }

    with patch("app.services.moderation_service.emit_product_blocked_to_b2b"):
        async with await make_client() as client:
            resp = await client.post(
                _BLOCK_URL,
                json=body,
                headers=SERVICE_KEY_HEADER,
            )

    assert resp.status_code == 200
    assert len(product.field_reports) == 2
    assert product.field_reports[0]["field_path"] == "title"
    assert product.field_reports[1]["field_path"] == "description"


async def test_block_triggers_product_blocked_to_b2c(override_db):
    """
    cascade: block_triggers_product_blocked_to_b2c — проверка fire-and-forget вызова.
    """
    ticket  = make_ticket(status=TicketStatus.IN_REVIEW)
    product = make_product(status=ProductStatus.ON_MODERATION)
    reason  = make_reason(hard_block=True)

    db = _db_for_block(ticket, product=product, reason=reason)
    app.dependency_overrides[get_db] = lambda: db

    with patch("app.services.moderation_service.emit_product_blocked_to_b2b") as mock_emit:
        async with await make_client() as client:
            resp = await client.post(
                _BLOCK_URL,
                json=BLOCK_BODY,
                headers=SERVICE_KEY_HEADER,
            )

    assert resp.status_code == 200
    mock_emit.assert_called_once_with(PRODUCT_ID, hard_block=True)