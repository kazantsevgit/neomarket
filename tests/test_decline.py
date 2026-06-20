"""
US-MOD-04: мягкая блокировка с замечаниями.

DoD:
  happy:
    - soft_block_transitions_to_blocked_with_field_reports
    - soft_block_emits_event_to_b2b
  unhappy:
    - soft_block_unknown_reason_returns_400
    - soft_block_others_card_returns_403
    - soft_block_invalid_field_name_returns_400
    - soft_block_hard_only_reason_returns_400
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies.db import get_db
from app.dependencies.moderator_auth import get_current_moderator_id
from app.main import app
from app.models.blocking_reason import BlockingReason
from app.models.product import Product, ProductStatus
from app.models.ticket import Ticket, TicketStatus
from app.models.ticket_field_report import TicketFieldReport

MODERATOR_ID = uuid.uuid4()
OTHER_MODERATOR_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()
TICKET_ID = uuid.uuid4()
BLOCKING_REASON_ID = uuid.uuid4()
_NOW = datetime.now(timezone.utc)

AUTH_HEADERS = {"Authorization": "Bearer moderator.jwt.token"}

_SKU_ID = uuid.uuid4()

BASE_BODY = {
    "blocking_reason_id": str(BLOCKING_REASON_ID),
    "moderator_comment": "Описание скопировано, цена занижена",
    "field_reports": [
        {
            "field_name": "description",
            "sku_id": None,
            "comment": "Текст скопирован с AliExpress",
        },
        {
            "field_name": "sku_price",
            "sku_id": str(_SKU_ID),
            "comment": "Цена ниже себестоимости",
        },
    ],
}


def make_ticket(
    *,
    status: TicketStatus = TicketStatus.IN_REVIEW,
    assigned_moderator_id: uuid.UUID = MODERATOR_ID,
    edit_pending: bool = False,
) -> MagicMock:
    ticket = MagicMock(spec=Ticket)
    ticket.id = TICKET_ID
    ticket.product_id = PRODUCT_ID
    ticket.seller_id = uuid.uuid4()
    ticket.category_id = None
    ticket.kind = MagicMock()
    ticket.kind.value = "CREATE"
    ticket.status = status
    ticket.assigned_moderator_id = assigned_moderator_id
    ticket.edit_pending = edit_pending
    ticket.moderator_comment = None
    ticket.blocking_reason_id = None
    ticket.decision_at = None
    ticket.updated_at = _NOW
    ticket.field_reports = []
    return ticket


def make_product(status: ProductStatus = ProductStatus.ON_MODERATION) -> MagicMock:
    product = MagicMock(spec=Product)
    product.id = PRODUCT_ID
    product.status = status
    product.blocking_reason_id = None
    product.blocking_reason = None
    product.moderator_comment = None
    product.field_reports = []
    return product


def make_reason(hard_block: bool = False) -> MagicMock:
    reason = MagicMock(spec=BlockingReason)
    reason.id = BLOCKING_REASON_ID
    reason.code = "POOR_DESCRIPTION"
    reason.title = "Описание не соответствует товару"
    reason.description = None
    reason.hard_block = hard_block
    reason.is_active = True
    return reason


@pytest.fixture(autouse=True)
def override_moderator():
    app.dependency_overrides[get_current_moderator_id] = lambda: MODERATOR_ID
    yield
    app.dependency_overrides.pop(get_current_moderator_id, None)


@pytest.fixture(autouse=True)
def override_db():
    fake_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


async def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _db_with_ticket(
    db: AsyncMock,
    ticket: MagicMock,
    product: MagicMock | None = None,
    reason: MagicMock | None = None,
) -> None:
    ticket_result = MagicMock()
    ticket_result.scalar_one_or_none.return_value = ticket
    db.execute = AsyncMock(return_value=ticket_result)

    def get_side_effect(model, pk):
        if model == Product:
            return product
        if model == BlockingReason:
            return reason
        return None

    db.get.side_effect = get_side_effect


# ── Happy ─────────────────────────────────────────────────────────────────────


async def test_soft_block_transitions_to_blocked_with_field_reports(override_db):
    """Happy: soft block → 200, статус BLOCKED, field_reports сохранены."""
    ticket = make_ticket()
    product = make_product()
    reason = make_reason(hard_block=False)
    _db_with_ticket(override_db, ticket, product=product, reason=reason)

    with patch("app.services.moderation_service.emit_product_blocked_to_b2b"):
        async with await make_client() as client:
            resp = await client.post(
                f"/api/v1/products/{PRODUCT_ID}/decline",
                json=BASE_BODY,
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["product_id"] == str(PRODUCT_ID)
    assert data["status"] == "BLOCKED"

    assert product.status == ProductStatus.BLOCKED
    assert product.blocking_reason_id == BLOCKING_REASON_ID
    assert product.moderator_comment == "Описание скопировано, цена занижена"
    assert len(product.field_reports) == 2
    assert product.field_reports[0]["field_name"] == "description"
    assert product.field_reports[1]["field_name"] == "sku_price"

    assert ticket.status == TicketStatus.BLOCKED
    assert ticket.moderator_comment == "Описание скопировано, цена занижена"
    assert ticket.decision_at is not None

    override_db.commit.assert_awaited_once()


async def test_soft_block_emits_event_to_b2b(override_db):
    """Happy: событие BLOCKED + hard_block=false уходит в B2B."""
    ticket = make_ticket()
    product = make_product()
    reason = make_reason(hard_block=False)
    _db_with_ticket(override_db, ticket, product=product, reason=reason)

    with patch("app.services.moderation_service.emit_product_blocked_to_b2b") as mock_emit:
        async with await make_client() as client:
            resp = await client.post(
                f"/api/v1/products/{PRODUCT_ID}/decline",
                json=BASE_BODY,
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 200
    mock_emit.assert_called_once_with(PRODUCT_ID, hard_block=False)


# ── Unhappy ───────────────────────────────────────────────────────────────────


async def test_soft_block_unknown_reason_returns_400(override_db):
    """Unknown blocking_reason_id → 400."""
    ticket = make_ticket()
    _db_with_ticket(override_db, ticket, reason=None)

    async with await make_client() as client:
        resp = await client.post(
            f"/api/v1/products/{PRODUCT_ID}/decline",
            json={"blocking_reason_id": str(uuid.uuid4())},
            headers=AUTH_HEADERS,
        )

    assert resp.status_code == 400
    assert resp.json()["code"] == "REASON_NOT_FOUND"


async def test_soft_block_others_card_returns_403(override_db):
    """Чужая карточка → 403."""
    ticket = make_ticket(assigned_moderator_id=OTHER_MODERATOR_ID)
    _db_with_ticket(override_db, ticket)

    async with await make_client() as client:
        resp = await client.post(
            f"/api/v1/products/{PRODUCT_ID}/decline",
            json=BASE_BODY,
            headers=AUTH_HEADERS,
        )

    assert resp.status_code == 403
    assert resp.json()["code"] == "NOT_ASSIGNED"


async def test_soft_block_invalid_field_name_returns_400(override_db):
    """Field name вне enum → 400 (Pydantic валидация)."""
    body = {
        "blocking_reason_id": str(BLOCKING_REASON_ID),
        "field_reports": [
            {"field_name": "invalid_field", "comment": "test"},
        ],
    }

    async with await make_client() as client:
        resp = await client.post(
            f"/api/v1/products/{PRODUCT_ID}/decline",
            json=body,
            headers=AUTH_HEADERS,
        )

    assert resp.status_code == 422


async def test_soft_block_hard_only_reason_returns_400(override_db):
    """hard_block=true причина → 400."""
    ticket = make_ticket()
    product = make_product()
    reason = make_reason(hard_block=True)
    _db_with_ticket(override_db, ticket, product=product, reason=reason)

    async with await make_client() as client:
        resp = await client.post(
            f"/api/v1/products/{PRODUCT_ID}/decline",
            json=BASE_BODY,
            headers=AUTH_HEADERS,
        )

    assert resp.status_code == 400
    assert resp.json()["code"] == "REASON_IS_HARD_BLOCK"
