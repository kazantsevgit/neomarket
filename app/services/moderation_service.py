"""
Сервис обработки событий модерации.

ADR (для PR):
  Рассматривались три подхода к гарантированию идемпотентности:
  1. Таблица processed_events по idempotency_key — явная запись о каждом
     обработанном событии, TTL управляется отдельно, читаемая история обработки.
  2. Поле last_event_key в модели Product — экономия места (одна колонка вместо
     таблицы), но TTL не реализуется, и сложнее отследить историю событий.
  3. Upsert с условием (ON CONFLICT DO NOTHING по составному ключу product_id +
     event_type) — нет TTL, требует сложных составных индексов.

  Выбран вариант 1 (таблица processed_events).
  Критерии:
  - Риск race-condition: минимален — уникальный constraint по idempotency_key даёт
    гарантию "ровно один раз" даже при параллельных запросах от Moderation.
  - Сложность поддержки: минимальна — отдельная таблица проще тестировать и
    мониторить, TTL-чистка не затрагивает таблицу Product.
"""
import logging
import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.moderation_event import ModerationEventIdempotency
from app.models.product import Product, ProductStatus
from app.schemas.moderation import FieldReport, ModerationEventRequest, ModerationEventType

logger = logging.getLogger(__name__)


def emit_product_blocked_to_b2c(product_id: uuid.UUID) -> None:
    """
    Заглушка — каскадное событие PRODUCT_BLOCKED в B2C.
    В production здесь HTTP/gRPC вызов или публикация в очередь.
    """
    pass  # pragma: no cover


async def apply_moderation_decision(
    db: AsyncSession,
    payload: ModerationEventRequest,
) -> None:
    """
    Применяет решение модерации к товару.

    1. Проверяем idempotency_key — если уже обрабатывали, возвращаемся без изменений.
    2. Загружаем товар из БД.
    3. В зависимости от event_type и hard_block:
       - MODERATED: status=MODERATED, очищаем blocking_reason/field_reports.
       - BLOCKED + hard_block=false: status=BLOCKED, сохраняем field_reports, каскад в B2C.
       - BLOCKED + hard_block=true: status=HARD_BLOCKED, каскад в B2C.
    4. Сохраняем idempotency-запись.
    """
    # ── 1. Idempotency check ─────────────────────────────────────────────────
    existing: Optional[ModerationEventIdempotency] = await db.get(
        ModerationEventIdempotency, payload.idempotency_key
    )
    if existing is not None:
        logger.info(
            "duplicate moderation event idempotency_key=%s, skipping",
            payload.idempotency_key,
        )
        return

    # ── 2. Загружаем товар ───────────────────────────────────────────────────
    product: Optional[Product] = await db.get(Product, payload.product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    # ── 3. Применяем решение ──────────────────────────────────────────────────
    if payload.event_type == ModerationEventType.MODERATED:
        product.status = ProductStatus.MODERATED
        product.blocking_reason_id = None
        product.blocking_reason = None
        product.moderator_comment = payload.moderator_comment
        product.field_reports = []

    elif payload.event_type == ModerationEventType.BLOCKED:
        if payload.hard_block:
            product.status = ProductStatus.HARD_BLOCKED
        else:
            product.status = ProductStatus.BLOCKED

        product.blocking_reason_id = payload.blocking_reason_id
        product.moderator_comment = payload.moderator_comment

        # Сохраняем field_reports если есть
        if payload.field_reports:
            product.field_reports = [
                {
                    "field_name": fr.field_name,
                    "sku_id": str(fr.sku_id) if fr.sku_id else None,
                    "comment": fr.comment,
                }
                for fr in payload.field_reports
            ]
        else:
            product.field_reports = []

        # Каскадное событие в B2C
        emit_product_blocked_to_b2c(product.id)

    # ── 4. Сохраняем idempotency-запись ──────────────────────────────────────
    db.add(
        ModerationEventIdempotency(
            idempotency_key=payload.idempotency_key,
            product_id=payload.product_id,
            event_type=payload.event_type.value,
        )
    )

    await db.commit()
    logger.info(
        "applied moderation decision product_id=%s event_type=%s",
        payload.product_id,
        payload.event_type.value,
    )
