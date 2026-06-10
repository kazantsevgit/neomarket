import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.b2b_event_idempotency import B2BEventIdempotency
from app.models.field_report import FieldReport
from app.models.product_moderation import ModerationStatus, ProductModeration
from app.schemas.b2b_event import B2BEventType, B2BProductEventRequest
from app.services.b2b_client import B2BUnavailableError, fetch_product_from_b2b

logger = logging.getLogger(__name__)


async def handle_b2b_event(
    db: AsyncSession,
    body: B2BProductEventRequest,
) -> None:
    idem = await db.get(B2BEventIdempotency, (body.product_id, body.date))
    if idem is not None:
        logger.info(
            "duplicate b2b event product_id=%s date=%s, skipping",
            body.product_id, body.date,
        )
        return

    if body.event == B2BEventType.CREATED:
        await _handle_created(db=db, body=body)
    elif body.event == B2BEventType.EDITED:
        await _handle_edited(db=db, body=body)
    elif body.event == B2BEventType.DELETED:
        await _handle_deleted(db=db, body=body)

    db.add(B2BEventIdempotency(
        product_id=body.product_id,
        event_date=body.date,
        event_type=body.event.value,
    ))
    await db.commit()


async def _handle_created(
    db: AsyncSession,
    body: B2BProductEventRequest,
) -> None:
    result = await db.execute(
        select(ProductModeration).where(
            ProductModeration.product_id == body.product_id
        ).limit(1)
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        if existing.status == ModerationStatus.HARD_BLOCKED:
            logger.info(
                "ignoring CREATED for HARD_BLOCKED product_id=%s", body.product_id
            )
            return
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DUPLICATE_CREATED",
                "message": "Product already has a moderation ticket",
            },
        )

    product_data = await _fetch_product(body.product_id)
    ticket = ProductModeration(
        id=uuid.uuid4(),
        product_id=body.product_id,
        seller_id=body.seller_id,
        json_before=None,
        json_after=product_data,
        status=ModerationStatus.PENDING,
        queue_priority=1,
        date_created=datetime.now(timezone.utc),
        date_updated=datetime.now(timezone.utc),
    )
    db.add(ticket)


async def _handle_edited(
    db: AsyncSession,
    body: B2BProductEventRequest,
) -> None:
    result = await db.execute(
        select(ProductModeration).where(
            ProductModeration.product_id == body.product_id
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
            "ignoring EDITED for HARD_BLOCKED product_id=%s", body.product_id
        )
        return

    old_status = existing.status

    product_data = await _fetch_product(body.product_id)

    new_priority = existing.queue_priority
    if old_status == ModerationStatus.BLOCKED:
        new_priority = 2
    elif old_status == ModerationStatus.MODERATED:
        total_active = _total_active_quantity(product_data)
        new_priority = 3 if total_active > 0 else 4

    existing.json_before = existing.json_after
    existing.json_after = product_data
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
    body: B2BProductEventRequest,
) -> None:
    result = await db.execute(
        select(ProductModeration).where(
            ProductModeration.product_id == body.product_id
        ).limit(1)
    )
    existing = result.scalar_one_or_none()

    if existing is None:
        logger.info(
            "DELETED for unknown product_id=%s, idempotent", body.product_id
        )
        return

    await db.delete(existing)


async def _fetch_product(product_id: uuid.UUID) -> dict[str, Any] | None:
    try:
        data = await fetch_product_from_b2b(product_id)
    except B2BUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "B2B_UNAVAILABLE",
                "message": "Failed to fetch product data from B2B",
            },
        )
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "B2B_PRODUCT_NOT_FOUND",
                "message": f"Product {product_id} not found in B2B",
            },
        )
    return data


def _total_active_quantity(product_data: dict[str, Any]) -> int:
    skus = product_data.get("skus", [])
    total = 0
    for sku in skus:
        stock = sku.get("stock_quantity", 0) or 0
        reserved = sku.get("reserved_quantity", 0) or 0
        total += max(0, stock - reserved)
    return total
