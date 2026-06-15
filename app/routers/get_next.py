import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.db import get_db
from app.dependencies.moderator_auth import get_current_moderator_id
from app.schemas.moderation import GetNextRequest, GetNextResponse
from app.services.get_next_service import claim_next_card

router = APIRouter(tags=["product_moderation"])


@router.post(
    "/api/v1/queue/claim",
    response_model=GetNextResponse,
    status_code=status.HTTP_200_OK,
    responses={
        204: {"description": "Очередь пуста"},
        400: {"description": "Невалидный queue_id"},
        409: {"description": "У модератора уже есть карточка IN_REVIEW"},
    },
)
async def get_next_card(
    body: GetNextRequest | None = None,
    moderator_id: uuid.UUID = Depends(get_current_moderator_id),
    db: AsyncSession = Depends(get_db),
) -> GetNextResponse | Response:
    queue_id = body.queue_priority if body is not None else None
    card = await claim_next_card(
        db=db,
        moderator_id=moderator_id,
        queue_id=queue_id,
    )
    if card is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return card
