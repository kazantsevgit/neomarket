"""
US-MOD-03: одобрение товара модератором.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ticket import Ticket, TicketStatus
from app.schemas.moderation import ApproveRequest, ApproveResponse
from app.services.b2b_moderation_client import (
    B2BModerationDeliveryError,
    b2b_delivery_http_exception,
    send_moderated_event_to_b2b,
)

logger = logging.getLogger(__name__)


async def _product_has_skus(product_id: uuid.UUID) -> bool:
    """Проверка наличия SKU у товара в B2B (канон-flow шаг 6)."""
    url = f"{settings.B2B_URL.rstrip('/')}/api/v1/products/{product_id}"
    try:
        async with httpx.AsyncClient(timeout=settings.B2B_HTTP_TIMEOUT) as client:
            resp = await client.get(
                url,
                headers={"X-Service-Key": settings.B2B_SERVICE_KEY},
            )
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        data = resp.json()
        return len(data.get("skus") or []) > 0
    except Exception as exc:
        logger.error("failed to fetch product from B2B product_id=%s: %s", product_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "Unable to verify product SKUs in B2B"},
        ) from exc


async def approve_product(
    db: AsyncSession,
    product_id: uuid.UUID,
    moderator_id: uuid.UUID,
    body: ApproveRequest | None,
) -> ApproveResponse:
    """
    IN_REVIEW → MODERATED (через событие в B2B).

    Статус тикета обновляется только после успешной доставки события в B2B.
    """
    result = await db.execute(select(Ticket).where(Ticket.product_id == product_id))
    ticket: Optional[Ticket] = result.scalar_one_or_none()

    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Product not found in moderation queue"},
        )

    if ticket.status == TicketStatus.HARD_BLOCKED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "Product is permanently blocked"},
        )

    if ticket.status != TicketStatus.IN_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "Product is not in review status"},
        )

    if ticket.assigned_moderator_id != moderator_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "This moderation card is not assigned to you"},
        )

    if ticket.edit_pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "Product was edited during review"},
        )

    if not await _product_has_skus(product_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "Product has no SKUs, cannot approve"},
        )

    comment = body.moderator_comment if body else None
    idempotency_key = uuid.uuid5(uuid.NAMESPACE_URL, f"approve:{ticket.id}")

    try:
        await send_moderated_event_to_b2b(
            product_id=product_id,
            moderator_id=moderator_id,
            moderator_comment=comment,
            idempotency_key=idempotency_key,
        )
    except B2BModerationDeliveryError:
        raise b2b_delivery_http_exception() from None

    now = datetime.now(timezone.utc)
    ticket.status = TicketStatus.APPROVED
    ticket.moderator_comment = comment
    ticket.blocking_reason_id = None
    ticket.decision_at = now
    ticket.updated_at = now
    ticket.field_reports.clear()

    await db.commit()

    return ApproveResponse(product_id=product_id, status="MODERATED")
