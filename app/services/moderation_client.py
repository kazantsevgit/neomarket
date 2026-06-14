"""
Клиент для отправки события CREATED в сервис Moderation.

ADR (выбор способа доставки события):
  Рассматривались три варианта:
  1. Синхронный HTTP POST в обработчике — прост, но если Moderation
     недоступна, весь запрос падает с 5xx и SKU не сохраняется.
  2. Outbox-pattern — надёжно (событие и SKU в одной транзакции),
     но требует outbox-таблицы и фонового воркера.
  3. Fire-and-forget (asyncio.create_task) — SKU сохраняется всегда;
     при недоступности Moderation событие теряется без retry.

  Выбор: fire-and-forget для первой итерации.
  Критерии:
  - Сложность: нулевая дополнительная инфраструктура.
  - Устойчивость: SKU гарантированно создаётся; потеря события
    допустима на MVP (модератор может перезапустить сканирование).
  Следующая итерация — outbox без изменения контракта.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Путь согласно neomarket-moderation.yaml (IncomingB2BEvent endpoint)
_MODERATION_EVENTS_PATH = "/api/v1/b2b/events"


async def _send(payload: dict[str, Any]) -> None:
    """Внутренняя корутина — вызывается через create_task (fire-and-forget)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                settings.MODERATION_URL + _MODERATION_EVENTS_PATH,
                json=payload,
                headers={
                    "X-Service-Key": settings.MODERATION_SERVICE_KEY,
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            logger.info("moderation event sent idempotency_key=%s", payload.get("idempotency_key"))
    except Exception as exc:
        logger.error("failed to send moderation event: %s", exc)


def emit_product_created(
    *,
    product_id: uuid.UUID,
    seller_id: uuid.UUID,
    category_id: uuid.UUID,
    title: str,
    sku_id: uuid.UUID,
    price: int,
    occurred_at: datetime | None = None,
) -> None:
    """
    Отправляет событие CREATED в Moderation (fire-and-forget).

    Структура тела соответствует схеме IncomingB2BEvent (neomarket-moderation.yaml:478-497):
      - event_type: "CREATED"
      - idempotency_key: str(product_id) — повторный вызов идемпотентен
      - product_id, seller_id, category_id, title — атрибуты товара
      - sku_id, price — первый SKU, инициировавший переход в ON_MODERATION

    Примечание: product_id=None vs seller_id=None — оба случая объединены
    в 404, чтобы не раскрывать чужие product_id (IDOR-защита).
    """
    ts = occurred_at or datetime.now(timezone.utc)
    payload = {
        "event_type": "CREATED",
        "idempotency_key": str(product_id),
        "occurred_at": ts.isoformat(),
        "product_id": str(product_id),
        "seller_id": str(seller_id),
        "category_id": str(category_id),
        "title": title,
        "sku_id": str(sku_id),
        "price": price,
    }
    asyncio.create_task(_send(payload))


def emit_product_deleted(
    *,
    product_id: uuid.UUID,
    seller_id: uuid.UUID,
    category_id: uuid.UUID,
    title: str,
    occurred_at: datetime | None = None,
) -> None:
    """
    Отправляет событие DELETED в Moderation (fire-and-forget).
    Вызывается при удалении последнего SKU товара в статусе ON_MODERATION.
    """
    ts = occurred_at or datetime.now(timezone.utc)
    payload = {
        "event_type": "DELETED",
        "idempotency_key": str(product_id),
        "occurred_at": ts.isoformat(),
        "product_id": str(product_id),
        "seller_id": str(seller_id),
        "category_id": str(category_id),
        "title": title,
    }
    asyncio.create_task(_send(payload))


def emit_product_edited(
    *,
    product_id: uuid.UUID,
    seller_id: uuid.UUID,
    category_id: uuid.UUID,
    title: str,
    sku_id: uuid.UUID,
    price: int,
    occurred_at: datetime | None = None,
) -> None:
    """
    Отправляет событие EDITED в Moderation (fire-and-forget).
    Вызывается при:
    - PUT /products/{id} (редактирование одобренного/заблокированного товара)
    - PUT /skus/{id} (редактирование SKU одобренного/заблокированного товара)
    - POST /skus (добавление SKU к MODERATED/BLOCKED товару)
    """
    ts = occurred_at or datetime.now(timezone.utc)
    payload = {
        "event_type": "EDITED",
        "idempotency_key": str(uuid.uuid4()),  # каждое редактирование уникально
        "occurred_at": ts.isoformat(),
        "product_id": str(product_id),
        "seller_id": str(seller_id),
        "category_id": str(category_id),
        "title": title,
        "sku_id": str(sku_id),
        "price": price,
    }
    asyncio.create_task(_send(payload))


async def _send_b2c(payload: dict) -> None:
    """Отправка события в B2C (fire-and-forget)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                settings.B2C_URL + "/api/v1/b2b/events",
                json=payload,
                headers={
                    "X-Service-Key": settings.B2C_SERVICE_KEY,
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
    except Exception as exc:
        logger.error("failed to send b2c event: %s", exc)


def emit_product_deleted_to_b2c(
    *,
    product_id: uuid.UUID,
    sku_ids: list,
    occurred_at: datetime | None = None,
) -> None:
    """
    Событие PRODUCT_DELETED в B2C.
    B2C использует sku_ids для пометки корзин.
    idempotency_key обязателен по схеме B2BEvent.
    """
    ts = occurred_at or datetime.now(timezone.utc)
    payload = {
        "event_type": "PRODUCT_DELETED",
        "idempotency_key": str(uuid.uuid4()),
        "occurred_at": ts.isoformat(),
        "product_id": str(product_id),
        "sku_ids": [str(s) for s in sku_ids],
    }
    asyncio.create_task(_send_b2c(payload))
