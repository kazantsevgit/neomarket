import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.db import get_db
from app.dependencies.moderator_auth import get_current_moderator_id
from app.schemas.moderation import ApproveRequest, ApproveResponse
from app.services.approve_service import approve_product

router = APIRouter(tags=["moderation_approve"])


@router.post(
    "/api/v1/products/{product_id}/approve",
    response_model=ApproveResponse,
    status_code=status.HTTP_200_OK,
    summary="Одобрить товар (MOD-3)",
)
async def approve_product_endpoint(
    product_id: uuid.UUID,
    body: ApproveRequest | None = None,
    moderator_id: uuid.UUID = Depends(get_current_moderator_id),
    db: AsyncSession = Depends(get_db),
) -> ApproveResponse:
    return await approve_product(
        db=db,
        product_id=product_id,
        moderator_id=moderator_id,
        body=body,
    )
