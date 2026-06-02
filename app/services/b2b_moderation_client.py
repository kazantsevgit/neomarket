"""
Синхронная доставка решения MODERATED в B2B (US-MOD-03).

ADR (для PR):
  1. Синхронный POST в обработчике approve — модератор сразу видит ошибку;
     при отказе B2B статус тикета не меняется (IN_REVIEW сохраняется).
  2. Outbox-pattern — надёжнее при падении B2B, но нужна таблица и воркер.
  3. Event-bus — масштабируемо, но избыточно для первой итерации.

  Выбор: синхронный POST.
  Критерии:
  - Надёжность: идемпотентность через idempotency_key на стороне B2B;
    повтор approve с тем же ключом не дублирует публикацию.
  - Время отклика: модератор получает финальный ответ в одном запросе.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException, status

from app.config import settings

logger = logging.getLogger(__name__)

_B2B_EVENTS_PATH = "/api/v1/moderation/events"


class B2BModerationDeliveryError(Exception):
    """B2B вернул ошибку или недоступен."""


async def send_moderated_event_to_b2b(
    *,
    product_id: uuid.UUID,
    moderator_id: uuid.UUID,
    moderator_comment: str | None,
    idempotency_key: uuid.UUID,
) -> None:
    """POST MODERATED в B2B. При ошибке вызывающий код отдаёт 500 модератору."""
    payload = {
        "idempotency_key": str(idempotency_key),
        "product_id": str(product_id),
        "event_type": "MODERATED",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "moderator_id": str(moderator_id),
        "moderator_comment": moderator_comment,
    }
    url = settings.B2B_URL.rstrip("/") + _B2B_EVENTS_PATH
    try:
        async with httpx.AsyncClient(timeout=settings.B2B_HTTP_TIMEOUT) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={
                    "X-Service-Key": settings.MODERATION_SERVICE_KEY,
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
        logger.info(
            "MODERATED event delivered product_id=%s idempotency_key=%s",
            product_id,
            idempotency_key,
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "B2B rejected MODERATED event product_id=%s status=%s body=%s",
            product_id,
            exc.response.status_code,
            exc.response.text,
        )
        raise B2BModerationDeliveryError(str(exc)) from exc
    except Exception as exc:
        logger.error("B2B unavailable for MODERATED product_id=%s: %s", product_id, exc)
        raise B2BModerationDeliveryError(str(exc)) from exc


def b2b_delivery_http_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"error": "Failed to deliver moderation decision to B2B"},
    )
