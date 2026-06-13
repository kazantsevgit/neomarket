import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.b2b_event_idempotency import B2BEventIdempotency
from app.models.field_report import FieldReport
from app.models.product_moderation import ModerationStatus, ProductModeration
from app.schemas.b2b_event import (
    EventProductCreated,
    EventProductDeleted,
    EventProductEdited,
    IncomingB2BEvent,
    IncomingB2BEventType,
)

logger = logging.getLogger(__name__)


async def handle_b2b_event(
    db: AsyncSession,
    body: IncomingB2BEvent,
) -> None:
    idem = await db.get(B2BEventIdempotency, body.idempotency_key)
    if idem is not None:
        logger.info(
            "duplicate b2b event idempotency_key=%s, skipping",
            body.idempotency_key,
        )
        return

    if body.event_type == IncomingB2BEventType.PRODUCT_CREATED:
        await _handle_created(db=db, body=body)
    elif body.event_type == IncomingB2BEventType.PRODUCT_EDITED:
        await _handle_edited(db=db, body=body)
    elif body.event_type == IncomingB2BEventType.PRODUCT_DELETED:
        await _handle_deleted(db=db, body=body)

    db.add(B2BEventIdempotency(
        idempotency_key=body.idempotency_key,
        event_type=body.event_type.value,
    ))
    await db.commit()


async def _handle_created(
    db: AsyncSession,
    body: IncomingB2BEvent,
) -> None:
    payload: EventProductCreated = body.payload
    result = await db.execute(
        select(ProductModeration).where(
            ProductModeration.product_id == payload.product_id
        ).limit(1)
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        if existing.status == ModerationStatus.HARD_BLOCKED:
            logger.info(
                "ignoring CREATED for HARD_BLOCKED product_id=%s", payload.product_id
            )
            return
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DUPLICATE_CREATED",
                "message": "Product already has a moderation ticket",
            },
        )

    ticket = ProductModeration(
        id=uuid.uuid4(),
        product_id=payload.product_id,
        seller_id=payload.seller_id,
        json_before=None,
        json_after=payload.json_after,
        status=ModerationStatus.PENDING,
        queue_priority=payload.queue_priority or 3,
        date_created=datetime.now(timezone.utc),
        date_updated=datetime.now(timezone.utc),
    )
    db.add(ticket)


async def _handle_edited(
    db: AsyncSession,
    body: IncomingB2BEvent,
) -> None:
    payload: EventProductEdited = body.payload
    result = await db.execute(
        select(ProductModeration).where(
            ProductModeration.product_id == payload.product_id
        ).limit(1)
    )
    existing = result.scalar_one_or_none()

    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "TICKET_NOT_FOUND",
                "message": "No moderation ticket found for this product",
            },
        )

    if existing.status == ModerationStatus.HARD_BLOCKED:
        logger.info(
            "ignoring EDITED for HARD_BLOCKED product_id=%s", payload.product_id
        )
        return

    old_status = existing.status

    new_priority = existing.queue_priority
    if old_status == ModerationStatus.BLOCKED:
        new_priority = 2
    elif old_status == ModerationStatus.MODERATED:
        new_priority = payload.queue_priority or 3

    existing.json_before = payload.json_before
    existing.json_after = payload.json_after
    existing.status = ModerationStatus.PENDING
    existing.queue_priority = new_priority
    existing.moderator_id = None
    existing.date_updated = datetime.now(timezone.utc)

    await db.execute(
        delete(FieldReport).where(
            FieldReport.product_moderation_id == existing.id
        )
    )


async def _handle_deleted(
    db: AsyncSession,
    body: IncomingB2BEvent,
) -> None:
    payload: EventProductDeleted = body.payload
    result = await db.execute(
        select(ProductModeration).where(
            ProductModeration.product_id == payload.product_id
        ).limit(1)
    )
    existing = result.scalar_one_or_none()

    if existing is None:
        logger.info(
            "DELETED for unknown product_id=%s, idempotent", payload.product_id
        )
        return

    await db.delete(existing)
