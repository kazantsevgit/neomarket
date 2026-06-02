"""
US-MOD-03: одобрение товара модератором.

DoD:
  happy:
    - approve_transitions_to_moderated_and_emits_event
  unhappy:
    - approve_others_card_returns_403
    - approve_after_edited_returns_409
    - approve_without_sku_returns_409
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies.db import get_db
from app.dependencies.moderator_auth import get_current_moderator_id
from app.main import app
from app.models.ticket import Ticket, TicketStatus

MODERATOR_ID = uuid.uuid4()
OTHER_MODERATOR_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()
TICKET_ID = uuid.uuid4()
_NOW = datetime.now(timezone.utc)

AUTH_HEADERS = {"Authorization": "Bearer moderator.jwt.token"}


def make_ticket(
    *,
    status: TicketStatus = TicketStatus.IN_REVIEW,
    assigned_moderator_id: uuid.UUID = MODERATOR_ID,
    edit_pending: bool = False,
) -> MagicMock:
    ticket = MagicMock(spec=Ticket)
    ticket.id = TICKET_ID
    ticket.product_id = PRODUCT_ID
    ticket.status = status
    ticket.assigned_moderator_id = assigned_moderator_id
    ticket.edit_pending = edit_pending
    ticket.moderator_comment = None
    ticket.blocking_reason_id = uuid.uuid4()
    ticket.decision_at = None
    ticket.updated_at = _NOW
    ticket.field_reports = []
    return ticket


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


def _db_with_ticket(db: AsyncMock, ticket: MagicMock) -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = ticket
    db.execute = AsyncMock(return_value=result)


async def test_approve_transitions_to_moderated_and_emits_event(override_db):
    """Happy: B2B получает MODERATED, тикет APPROVED, field_reports очищены."""
    ticket = make_ticket()
    _db_with_ticket(override_db, ticket)

    with patch("app.services.approve_service._product_has_skus", new_callable=AsyncMock, return_value=True), \
         patch(
             "app.services.approve_service.send_moderated_event_to_b2b",
             new_callable=AsyncMock,
         ) as mock_send:

        async with await make_client() as client:
            resp = await client.post(
                f"/api/v1/products/{PRODUCT_ID}/approve",
                json={"moderator_comment": "Товар соответствует требованиям"},
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["product_id"] == str(PRODUCT_ID)
    assert data["status"] == "MODERATED"

    assert ticket.status == TicketStatus.APPROVED
    assert ticket.moderator_comment == "Товар соответствует требованиям"
    assert ticket.blocking_reason_id is None
    assert ticket.field_reports == []
    assert ticket.decision_at is not None

    mock_send.assert_awaited_once()
    call_kwargs = mock_send.await_args.kwargs
    assert call_kwargs["product_id"] == PRODUCT_ID
    assert call_kwargs["moderator_id"] == MODERATOR_ID
    assert call_kwargs["moderator_comment"] == "Товар соответствует требованиям"
    assert call_kwargs["idempotency_key"] == uuid.uuid5(uuid.NAMESPACE_URL, f"approve:{TICKET_ID}")

    override_db.commit.assert_awaited_once()


async def test_approve_others_card_returns_403(override_db):
    """Чужая карточка → 403."""
    ticket = make_ticket(assigned_moderator_id=OTHER_MODERATOR_ID)
    _db_with_ticket(override_db, ticket)

    async with await make_client() as client:
        resp = await client.post(
            f"/api/v1/products/{PRODUCT_ID}/approve",
            headers=AUTH_HEADERS,
        )

    assert resp.status_code == 403
    assert resp.json()["error"] == "This moderation card is not assigned to you"


async def test_approve_after_edited_returns_409(override_db):
    """Продавец отредактировал во время review → 409."""
    ticket = make_ticket(edit_pending=True)
    _db_with_ticket(override_db, ticket)

    with patch("app.services.approve_service._product_has_skus", new_callable=AsyncMock, return_value=True):
        async with await make_client() as client:
            resp = await client.post(
                f"/api/v1/products/{PRODUCT_ID}/approve",
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 409
    assert resp.json()["error"] == "Product was edited during review"


async def test_approve_without_sku_returns_409(override_db):
    """Товар без SKU → 409."""
    ticket = make_ticket()
    _db_with_ticket(override_db, ticket)

    with patch("app.services.approve_service._product_has_skus", new_callable=AsyncMock, return_value=False):
        async with await make_client() as client:
            resp = await client.post(
                f"/api/v1/products/{PRODUCT_ID}/approve",
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 409
    assert resp.json()["error"] == "Product has no SKUs, cannot approve"
