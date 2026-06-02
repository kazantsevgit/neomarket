import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies.db import get_db
from app.schemas.moderation import DeclineRequest, DeclineResponse
from app.services.moderation_service import hard_block_product

router = APIRouter(tags=["decline"])


@router.post(
    "/api/v1/products/{product_id}/decline",
    response_model=DeclineResponse,
    status_code=status.HTTP_200_OK,
)
async def decline_product(
    product_id: uuid.UUID,
    body: DeclineRequest,
    x_service_key: str | None = Header(None, alias="X-Service-Key"),
    db: AsyncSession = Depends(get_db),
) -> DeclineResponse:
    if x_service_key is None or x_service_key != settings.MODERATION_SERVICE_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or missing X-Service-Key"},
        )

    result = await hard_block_product(db=db, product_id=product_id, request=body)
    return result
