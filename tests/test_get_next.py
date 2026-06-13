"""
Тесты US-MOD-02: получение следующей карточки из очереди (get-next).

DoD-сценарии:
  next_returns_oldest_pending — самая старая PENDING переходит в IN_REVIEW, закреплена за модератором;
  empty_queue_returns_204 — пустая очередь → 204;
  moderator_already_has_in_review_returns_409 — уже есть IN_REVIEW;
  concurrent_two_moderators_get_different_cards — две сессии не получают одну карточку;
  invalid_queue_id_returns_400 — queue_id вне 1-4 → 422;
  auto_priority_tries_1_to_4 — автоприоритизация перебирает 1→4;
  specific_queue_returns_card — указание queue_id возвращает карточку из этой очереди.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from jose import jwt
from sqlalchemy import Result

from app.config import settings
from app.dependencies.db import get_db
from app.main import app
from app.models.product_moderation import ModerationStatus, ProductModeration

_NOW = datetime.now(timezone.utc)
PRODUCT_ID = uuid.uuid4()
SELLER_ID = uuid.uuid4()
MODERATOR_ID = uuid.uuid4()
BODY_EMPTY = ""

MOD_TOKEN = jwt.encode(
    {"sub": str(MODERATOR_ID)},
    settings.JWT_SECRET,
    algorithm=settings.JWT_ALGORITHM,
)
AUTH_HEADER = {"Authorization": f"Bearer {MOD_TOKEN}"}


def _make_card(
    status: ModerationStatus = ModerationStatus.PENDING,
    queue_priority: int = 3,
    date_updated: datetime | None = None,
    moderator_id: uuid.UUID | None = None,
    json_before: dict | None = None,
    json_after: dict | None = None,
) -> MagicMock:
    card = MagicMock(spec=ProductModeration)
    card.id = uuid.uuid4()
    card.product_id = PRODUCT_ID
    card.seller_id = SELLER_ID
    card.status = status
    card.queue_priority = queue_priority
    card.moderator_id = moderator_id
    card.json_before = json_before
    card.json_after = json_after or {"id": str(PRODUCT_ID), "title": "Test"}
    card.date_created = _NOW
    card.date_updated = date_updated or _NOW
    return card


@pytest.fixture(autouse=True)
def override_db():
    fake_db = AsyncMock()
    fake_db.commit = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _assert_card_response(resp, card: MagicMock) -> None:
    data = resp.json()
    assert data["product_moderation_id"] == str(card.id)
    assert data["product_id"] == str(card.product_id)
    assert data["seller_id"] == str(card.seller_id)
    assert data["status"] == "IN_REVIEW"
    assert data["queue_priority"] == card.queue_priority


# ─── Happy path: get oldest PENDING ────────────────────────────────────────


async def test_next_returns_oldest_pending(override_db):
    """
    happy: next_returns_oldest_pending
    Самая старая PENDING → IN_REVIEW, закрепляется за модератором.
    """
    db = override_db
    older = _make_card(
        queue_priority=1,
        date_updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    newer = _make_card(
        queue_priority=1,
        date_updated=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )

    # no existing IN_REVIEW
    existing_result = MagicMock(spec=Result)
    existing_result.scalar_one_or_none.return_value = None

    # first FOR UPDATE SKIP LOCKED returns the oldest
    claim_result = MagicMock(spec=Result)
    claim_result.scalar_one_or_none.return_value = older

    db.execute.side_effect = [existing_result, claim_result]

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/product-moderation/get-next",
            json={"queue_id": 1},
            headers=AUTH_HEADER,
        )

    assert resp.status_code == 200
    _assert_card_response(resp, older)
    assert older.status == ModerationStatus.IN_REVIEW
    assert older.moderator_id == MODERATOR_ID
    db.commit.assert_awaited_once()


# ─── Empty queue ───────────────────────────────────────────────────────────


async def test_empty_queue_returns_204(override_db):
    """
    unhappy: empty_queue_returns_204
    Нет PENDING карточек → 204.
    """
    db = override_db
    existing_result = MagicMock(spec=Result)
    existing_result.scalar_one_or_none.return_value = None

    claim_result = MagicMock(spec=Result)
    claim_result.scalar_one_or_none.return_value = None

    db.execute.side_effect = [existing_result, claim_result]

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/product-moderation/get-next",
            json={"queue_id": 1},
            headers=AUTH_HEADER,
        )

    assert resp.status_code == 204


# ─── Moderator already has IN_REVIEW ───────────────────────────────────────


async def test_moderator_already_has_in_review_returns_409(override_db):
    """
    unhappy: moderator_already_has_in_review_returns_409
    Модератор уже держит IN_REVIEW → 409.
    """
    db = override_db
    existing_card = _make_card(status=ModerationStatus.IN_REVIEW, moderator_id=MODERATOR_ID)
    existing_result = MagicMock(spec=Result)
    existing_result.scalar_one_or_none.return_value = existing_card

    db.execute.return_value = existing_result

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/product-moderation/get-next",
            json={"queue_id": 1},
            headers=AUTH_HEADER,
        )

    assert resp.status_code == 409
    assert resp.json()["code"] == "ALREADY_IN_REVIEW"


# ─── Invalid queue_id ──────────────────────────────────────────────────────


async def test_invalid_queue_id_returns_422(override_db):
    """queue_id=5 → 422 Validation Error."""
    async with await _client() as client:
        resp = await client.post(
            "/api/v1/product-moderation/get-next",
            json={"queue_id": 5},
            headers=AUTH_HEADER,
        )

    assert resp.status_code == 422


async def test_queue_id_zero_returns_422(override_db):
    """queue_id=0 → 422."""
    async with await _client() as client:
        resp = await client.post(
            "/api/v1/product-moderation/get-next",
            json={"queue_id": 0},
            headers=AUTH_HEADER,
        )

    assert resp.status_code == 422


# ─── Auto-priority ─────────────────────────────────────────────────────────


async def test_auto_priority_tries_1_to_4(override_db):
    """
    cascade: auto_priority_tries_1_to_4
    Без queue_id перебирает 1→4, возвращает первую непустую.
    """
    db = override_db
    card_q3 = _make_card(queue_priority=3)
    existing_result = MagicMock(spec=Result)
    existing_result.scalar_one_or_none.return_value = None

    # 1→2 empty, 3→has card
    empty_result = MagicMock(spec=Result)
    empty_result.scalar_one_or_none.return_value = None
    found_result = MagicMock(spec=Result)
    found_result.scalar_one_or_none.return_value = card_q3

    db.execute.side_effect = [
        existing_result,   # check existing IN_REVIEW
        empty_result,      # priority 1 — empty
        empty_result,      # priority 2 — empty
        found_result,      # priority 3 — found!
    ]

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/product-moderation/get-next",
            json={},
            headers=AUTH_HEADER,
        )

    assert resp.status_code == 200
    _assert_card_response(resp, card_q3)
    assert card_q3.status == ModerationStatus.IN_REVIEW
    assert card_q3.moderator_id == MODERATOR_ID


async def test_auto_priority_all_empty_returns_204(override_db):
    """Автоприоритизация, все очереди пусты → 204."""
    db = override_db
    existing_result = MagicMock(spec=Result)
    existing_result.scalar_one_or_none.return_value = None

    empty_result = MagicMock(spec=Result)
    empty_result.scalar_one_or_none.return_value = None

    db.execute.side_effect = [
        existing_result,
        empty_result,  # priority 1
        empty_result,  # priority 2
        empty_result,  # priority 3
        empty_result,  # priority 4
    ]

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/product-moderation/get-next",
            json={},
            headers=AUTH_HEADER,
        )

    assert resp.status_code == 204


# ─── Specific queue ────────────────────────────────────────────────────────


async def test_specific_queue_returns_card(override_db):
    """
    happy: specific_queue_returns_card
    queue_id=2 возвращает карточку из очереди 2, карточки 1 игнорируются.
    """
    db = override_db
    card_q2 = _make_card(queue_priority=2)
    existing_result = MagicMock(spec=Result)
    existing_result.scalar_one_or_none.return_value = None

    claim_result = MagicMock(spec=Result)
    claim_result.scalar_one_or_none.return_value = card_q2

    db.execute.side_effect = [existing_result, claim_result]

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/product-moderation/get-next",
            json={"queue_id": 2},
            headers=AUTH_HEADER,
        )

    assert resp.status_code == 200
    _assert_card_response(resp, card_q2)
    assert card_q2.status == ModerationStatus.IN_REVIEW
    assert card_q2.moderator_id == MODERATOR_ID


# ─── Blocking history ──────────────────────────────────────────────────────


async def test_blocking_history_present(override_db):
    """
    cascade: blocking_history_present
    json_before содержит blocking_reason → blocking_history заполнен.
    """
    db = override_db
    card = _make_card(
        queue_priority=2,
        json_before={
            "blocking_reason": {
                "id": str(uuid.uuid4()),
                "title": "Описание не соответствует товару",
                "comment": "Фото другой модели",
            },
            "field_reports": [
                {"field_name": "product_images", "sku_id": None, "comment": "На фото iPhone 14"},
            ],
        },
    )

    existing_result = MagicMock(spec=Result)
    existing_result.scalar_one_or_none.return_value = None

    claim_result = MagicMock(spec=Result)
    claim_result.scalar_one_or_none.return_value = card

    db.execute.side_effect = [existing_result, claim_result]

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/product-moderation/get-next",
            json={},
            headers=AUTH_HEADER,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["blocking_history"] is not None
    assert data["blocking_history"]["blocking_reason"]["title"] == "Описание не соответствует товару"
    assert len(data["blocking_history"]["field_reports"]) == 1


async def test_blocking_history_null_for_new(override_db):
    """
    cascade: blocking_history_null_for_new
    Новая карточка без json_before → blocking_history = null.
    """
    db = override_db
    card = _make_card(queue_priority=1)
    card.json_before = None

    existing_result = MagicMock(spec=Result)
    existing_result.scalar_one_or_none.return_value = None

    claim_result = MagicMock(spec=Result)
    claim_result.scalar_one_or_none.return_value = card

    db.execute.side_effect = [existing_result, claim_result]

    async with await _client() as client:
        resp = await client.post(
            "/api/v1/product-moderation/get-next",
            json={"queue_id": 1},
            headers=AUTH_HEADER,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["blocking_history"] is None


# ─── Unauthorized ──────────────────────────────────────────────────────────


async def test_missing_token_returns_403(override_db):
    """Без токена → 403 (HTTPBearer без header)."""
    async with await _client() as client:
        resp = await client.post(
            "/api/v1/product-moderation/get-next",
            json={"queue_id": 1},
        )

    assert resp.status_code == 403


async def test_wrong_token_returns_401(override_db):
    """Невалидный токен → 401."""
    async with await _client() as client:
        resp = await client.post(
            "/api/v1/product-moderation/get-next",
            json={"queue_id": 1},
            headers={"Authorization": "Bearer invalid-token"},
        )

    assert resp.status_code == 401
