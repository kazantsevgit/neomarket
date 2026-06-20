import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies.db import get_db
from app.dependencies.moderator_auth import get_current_moderator_id
from app.schemas.moderation import (
    BlockDecisionRequest,
    DeclineProductRequest,
    DeclineProductResponse,
    TicketResponse,
)
from app.services.moderation_service import block_ticket, soft_block_product

router = APIRouter(tags=["tickets"])


@router.post(
    "/api/v1/tickets/{ticket_id}/block",
    response_model=TicketResponse,
    status_code=status.HTTP_200_OK,
)
async def block_ticket_endpoint(
    ticket_id: uuid.UUID,
    body: BlockDecisionRequest,
    x_service_key: str | None = Header(None, alias="X-Service-Key"),
    db: AsyncSession = Depends(get_db),
) -> TicketResponse:
    if x_service_key is None or x_service_key != settings.MODERATION_SERVICE_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or missing X-Service-Key"},
        )

    return await block_ticket(db=db, ticket_id=ticket_id, request=body)


@router.post(
    "/api/v1/products/{product_id}/decline",
    response_model=DeclineProductResponse,
    status_code=status.HTTP_200_OK,
    summary="Мягкая блокировка товара (US-MOD-04)",
)
async def decline_product_endpoint(
    product_id: uuid.UUID,
    body: DeclineProductRequest,
    moderator_id: uuid.UUID = Depends(get_current_moderator_id),
    db: AsyncSession = Depends(get_db),
) -> DeclineProductResponse:
    return await soft_block_product(
        db=db,
        product_id=product_id,
        moderator_id=moderator_id,
        request=body,
    )
