from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies.db import get_db
from app.schemas.moderation import ModerationEventRequest
from app.services.moderation_service import apply_moderation_decision

router = APIRouter(prefix="/api/v1/moderation", tags=["moderation_events"])


def _verify_service_key(x_service_key: str = Header(..., alias="X-Service-Key")) -> None:
    """Проверка ключа межсервисного взаимодействия."""
    if x_service_key != settings.MODERATION_SERVICE_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Service-Key",
        )


@router.post(
    "/events",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_verify_service_key)],
)
async def receive_moderation_event(
    body: ModerationEventRequest,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Приём событий MODERATED/BLOCKED от Moderation Service."""
    await apply_moderation_decision(db=db, payload=body)
