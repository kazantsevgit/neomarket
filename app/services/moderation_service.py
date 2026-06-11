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
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.blocking_reason import BlockingReason
from app.models.moderation_event import ModerationEventIdempotency
from app.models.product import Product, ProductStatus
from app.models.ticket import Ticket, TicketStatus
from app.models.ticket_field_report import TicketFieldReport
from app.schemas.moderation import (
    BlockDecisionRequest,
    BlockFieldReport,
    DeclineRequest,
    DeclineResponse,
    FieldReport,
    ModerationEventRequest,
    ModerationEventType,
    TicketResponse,
)

logger = logging.getLogger(__name__)


async def _send_product_blocked(product_id: uuid.UUID) -> None:
    """Реальная отправка PRODUCT_BLOCKED в B2C (fire-and-forget)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{settings.B2C_URL}/api/v1/b2b/events",
                json={
                    "idempotency_key": str(uuid.uuid4()),
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "event_type": "PRODUCT_BLOCKED",
                    "payload": {"product_id": str(product_id)},
                },
                headers={"X-Service-Key": settings.B2C_SERVICE_KEY},
            )
        logger.info("PRODUCT_BLOCKED sent product_id=%s", product_id)
    except Exception as exc:
        logger.error("failed to send PRODUCT_BLOCKED product_id=%s: %s", product_id, exc)


def emit_product_blocked_to_b2c(product_id: uuid.UUID) -> None:
    """Fire-and-forget каскадное событие в B2C."""
    asyncio.create_task(_send_product_blocked(product_id))


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

    # ── 3. Защита HARD_BLOCKED (терминальный статус) ─────────────────────────
    if product.status == ProductStatus.HARD_BLOCKED:
        logger.warning(
            "ignoring moderation event for HARD_BLOCKED product_id=%s", payload.product_id
        )
        # Сохраняем запись чтобы не штурмовали повторными попытками
        db.add(ModerationEventIdempotency(
            idempotency_key=payload.idempotency_key,
            product_id=payload.product_id,
            event_type=payload.event_type.value,
        ))
        await db.commit()
        return

    # ── 4. Применяем решение ──────────────────────────────────────────────────
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

    # ── 5. Сохраняем idempotency-запись ──────────────────────────────────────
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


async def hard_block_product(
    db: AsyncSession,
    product_id: uuid.UUID,
    request: DeclineRequest,
) -> DeclineResponse:
    """
    Жёсткая блокировка товара (US-MOD-05).

    1. Загружаем товар.
    2. Проверяем, что он не в HARD_BLOCKED (терминальный статус).
    3. Проверяем статус ON_MODERATION (IN_REVIEW в терминах тикета).
    4. Загружаем причину блокировки.
    5. Проверяем hard_block=true.
    6. Эмитируем событие BLOCKED + hard_block=true в B2B-обработчик.
    7. Ответ 200.
    """
    product: Optional[Product] = await db.get(Product, product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PRODUCT_NOT_FOUND", "message": "Product not found"},
        )

    if product.status == ProductStatus.HARD_BLOCKED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ALREADY_HARD_BLOCKED",
                "message": "Product is already HARD_BLOCKED",
            },
        )

    if product.status != ProductStatus.ON_MODERATION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "WRONG_STATUS",
                "message": f"Product must be ON_MODERATION, got {product.status.value}",
            },
        )

    reason: Optional[BlockingReason] = await db.get(
        BlockingReason, request.blocking_reason_id
    )
    if reason is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "REASON_NOT_FOUND", "message": "Blocking reason not found"},
        )
    if not reason.hard_block:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "NOT_HARD_BLOCK_REASON",
                "message": "Blocking reason is not a hard-block reason",
            },
        )

    # ── Эмитируем событие BLOCKED + hard_block=true в B2B-обработчик ──
    event = ModerationEventRequest(
        idempotency_key=uuid.uuid4(),
        product_id=product_id,
        event_type=ModerationEventType.BLOCKED,
        hard_block=True,
        blocking_reason_id=request.blocking_reason_id,
        moderator_comment=request.moderator_comment,
        field_reports=request.field_reports,
        occurred_at=datetime.now(timezone.utc),
    )
    await apply_moderation_decision(db=db, payload=event)

    logger.info("hard_blocked product_id=%s reason=%s", product_id, reason.code)

    return DeclineResponse(
        product_id=product_id,
        status=ProductStatus.HARD_BLOCKED.value,
    )


async def block_ticket(
    db: AsyncSession,
    ticket_id: uuid.UUID,
    request: BlockDecisionRequest,
) -> TicketResponse:
    """
    Блокировка товара по тикету (мягкая или жёсткая).

    Тип блокировки определяется по hard_block у выбранной BlockingReason.
    """
    ticket: Ticket | None = await db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TICKET_NOT_FOUND", "message": "Ticket not found"},
        )

    if ticket.status == TicketStatus.HARD_BLOCKED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ALREADY_HARD_BLOCKED", "message": "Ticket is already HARD_BLOCKED"},
        )

    if ticket.status != TicketStatus.IN_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "WRONG_STATUS",
                "message": f"Ticket must be IN_REVIEW, got {ticket.status.value}",
            },
        )

    product: Product | None = await db.get(Product, ticket.product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PRODUCT_NOT_FOUND", "message": "Product not found"},
        )

    reason_id = request.blocking_reason_ids[0]
    reason: BlockingReason | None = await db.get(BlockingReason, reason_id)
    if reason is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "REASON_NOT_FOUND", "message": "Blocking reason not found"},
        )

    is_hard = reason.hard_block

    new_status_p = ProductStatus.HARD_BLOCKED if is_hard else ProductStatus.BLOCKED
    new_status_t = TicketStatus.HARD_BLOCKED if is_hard else TicketStatus.BLOCKED

    product.status = new_status_p
    product.blocking_reason_id = reason_id
    product.blocking_reason = {
        "id": str(reason.id),
        "title": reason.title,
        "comment": request.comment or "",
    }
    product.moderator_comment = request.comment
    product.field_reports = [
        {"field_name": fr.field_name, "sku_id": None, "comment": fr.comment}
        for fr in (request.field_reports or [])
    ]

    now = datetime.now(timezone.utc)
    ticket.status = new_status_t
    ticket.blocking_reason_id = reason_id
    ticket.moderator_comment = request.comment
    ticket.decision_at = now
    ticket.updated_at = now

    ticket.field_reports.clear()
    for fr in (request.field_reports or []):
        tfr = TicketFieldReport(
            ticket_id=ticket.id,
            field_name=fr.field_name,
            comment=fr.comment,
        )
        ticket.field_reports.append(tfr)

    emit_product_blocked_to_b2c(product.id)

    await db.commit()

    logger.info(
        "block_ticket ticket_id=%s product_id=%s reason=%s hard=%s",
        ticket_id, product.id, reason.code, is_hard,
    )

    return TicketResponse(
        id=ticket.id,
        product_id=ticket.product_id,
        seller_id=ticket.seller_id,
        category_id=ticket.category_id,
        kind=ticket.kind.value,
        status=ticket.status.value,
        assigned_moderator_id=ticket.assigned_moderator_id,
        decision_at=ticket.decision_at,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )
