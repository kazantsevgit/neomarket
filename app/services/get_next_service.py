import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_moderation import ModerationStatus, ProductModeration
from app.schemas.moderation import BlockingHistory, BlockingHistoryBlockingReason, GetNextResponse

logger = logging.getLogger(__name__)


def _build_blocking_history(card: ProductModeration) -> BlockingHistory | None:
    if not card.json_before:
        return None
    blocking_reason = card.json_before.get("blocking_reason")
    if not blocking_reason:
        return None
    return BlockingHistory(
        blocking_reason=BlockingHistoryBlockingReason(
            id=blocking_reason["id"],
            title=blocking_reason["title"],
        ),
        moderator_comment=blocking_reason.get("comment"),
        field_reports=card.json_before.get("field_reports"),
        date_blocked=card.json_before.get("date_blocked"),
    )


def _card_to_response(card: ProductModeration) -> GetNextResponse:
    return GetNextResponse(
        id=card.id,
        product_moderation_id=card.id,
        product_id=card.product_id,
        seller_id=card.seller_id,
        kind="product",
        status=card.status.value,
        queue_priority=card.queue_priority,
        json_before=card.json_before,
        json_after=card.json_after,
        blocking_history=_build_blocking_history(card),
        date_created=card.date_created,
        date_updated=card.date_updated,
    )


async def claim_next_card(
    db: AsyncSession,
    moderator_id: uuid.UUID,
    queue_id: int | None,
) -> GetNextResponse | None:
    existing = await db.execute(
        select(ProductModeration).where(
            ProductModeration.moderator_id == moderator_id,
            ProductModeration.status == ModerationStatus.IN_REVIEW,
        ).limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ALREADY_IN_REVIEW",
                "message": "Moderator already has a card in review",
            },
        )

    priorities = [queue_id] if queue_id is not None else [1, 2, 3, 4]

    for priority in priorities:
        result = await db.execute(
            select(ProductModeration)
            .where(
                ProductModeration.status == ModerationStatus.PENDING,
                ProductModeration.queue_priority == priority,
            )
            .order_by(ProductModeration.date_updated.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        card = result.scalar_one_or_none()
        if card is not None:
            now = datetime.now(timezone.utc)
            card.status = ModerationStatus.IN_REVIEW
            card.moderator_id = moderator_id
            card.date_updated = now
            await db.commit()
            return _card_to_response(card)

    return None
