"""
Тесты endpoint POST /api/v1/moderation/events.

DoD-сценарии:
  happy:
    - moderated_event_clears_blocking_data
    - blocked_soft_saves_field_reports
    - blocked_hard_sets_terminal_status
  unhappy:
    - duplicate_event_same_idempotency_key_no_side_effects
    - missing_service_key_returns_401
  additional:
    - hard_blocked_product_rejects_seller_edits (интеграционный тест с PUT/DELETE)
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.dependencies.db import get_db
from app.models.product import Product, ProductStatus
from app.models.moderation_event import ModerationEventIdempotency

# ─── Константы ───────────────────────────────────────────────────────────────

_NOW            = datetime.now(timezone.utc)
PRODUCT_ID      = uuid.uuid4()
IDEM_KEY        = uuid.uuid4()
BLOCKING_REASON = uuid.uuid4()
MODERATOR_ID    = uuid.uuid4()

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

    with patch("app.services.moderation_service.emit_product_blocked_to_b2c") as mock_emit:
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

    # Каскадное событие в B2C
    mock_emit.assert_called_once_with(PRODUCT_ID)
    db.commit.assert_awaited_once()


async def test_blocked_hard_sets_terminal_status(override_db):
    """
    happy: blocked_hard_sets_terminal_status
    BLOCKED + hard_block=true → HARD_BLOCKED, каскад в B2C.
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

    with patch("app.services.moderation_service.emit_product_blocked_to_b2c") as mock_emit:
        async with await make_client() as client:
            resp = await client.post(
                "/api/v1/moderation/events",
                json=event,
                headers=SERVICE_KEY_HEADER,
            )

    assert resp.status_code == 204
    assert product.status == ProductStatus.HARD_BLOCKED
    assert product.blocking_reason_id == BLOCKING_REASON

    mock_emit.assert_called_once_with(PRODUCT_ID)
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
    assert resp.json()["detail"]["code"] == "UNAUTHORIZED"


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
    assert resp.json()["detail"]["code"] == "UNAUTHORIZED"


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
