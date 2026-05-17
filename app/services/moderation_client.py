"""
Клиент для отправки события CREATED в сервис Moderation.

ADR (выбор способа доставки события):
  Рассматривались три варианта:
  1. Синхронный HTTP POST в обработчике — просто, но если Moderation
     недоступна, весь запрос падает с 5xx и SKU не сохраняется (либо
     нужна ручная компенсация).
  2. Outbox-pattern — надёжно (событие сохраняется в БД вместе со SKU
     в одной транзакции, отдельный воркер доставляет его), но требует
     дополнительной инфраструктуры: таблица outbox + фоновый процесс.
  3. Fire-and-forget (asyncio.create_task) — SKU сохраняется в любом
     случае; если Moderation недоступна — событие теряется без retry.

  Выбор: fire-and-forget для первой итерации.
  Критерии:
  - Сложность реализации: минимальная — не нужны outbox-таблица и воркер.
  - Устойчивость к недоступности Moderation: SKU всегда сохраняется;
    потеря события допустима на этапе MVP, потому что модератор может
    вручную переотправить или перезапустить сканирование товаров.
  В следующей итерации можно перейти на outbox без изменения контракта.
"""

import asyncio
import logging
import uuid
from decimal import Decimal
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def _send(payload: dict[str, Any]) -> None:
    """Внутренняя корутина — вызывается через create_task."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                settings.MODERATION_URL + "/api/v1/events",
                json=payload,
                headers={
                    "X-Service-Key": settings.MODERATION_SERVICE_KEY,
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            logger.info("moderation event sent: %s", payload.get("idempotency_key"))
    except Exception as exc:
        # Логируем, но не роняем запрос — fire-and-forget
        logger.error("failed to send moderation event: %s", exc)


def emit_product_created(
    *,
    product_id: uuid.UUID,
    seller_id: uuid.UUID,
    category_id: uuid.UUID,
    title: str,
    images: list[str],
    sku_id: uuid.UUID,
    price: Decimal,
) -> None:
    """
    Отправляет событие CREATED в Moderation асинхронно (fire-and-forget).
    Idempotency-key = product_id, чтобы повторный вызов не дублировал запись.
    """
    payload = {
        "event": "CREATED",
        "idempotency_key": str(product_id),
        "product": {
            "id": str(product_id),
            "seller_id": str(seller_id),
            "category_id": str(category_id),
            "title": title,
            "images": images,
        },
        "sku": {
            "id": str(sku_id),
            "price": str(price),
        },
    }
    asyncio.create_task(_send(payload))