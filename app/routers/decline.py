import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies.db import get_db
from app.schemas.moderation import BlockDecisionRequest, TicketResponse
from app.services.moderation_service import block_ticket

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
