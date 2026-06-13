from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies.db import get_db
from app.schemas.b2b_event import IncomingB2BEvent
from app.services.b2b_event_service import handle_b2b_event

router = APIRouter(prefix="/api/v1/b2b", tags=["b2b_events"])


def _verify_b2b_service_key(
    x_service_key: str | None = Header(None, alias="X-Service-Key"),
) -> None:
    if x_service_key is None or x_service_key != settings.B2B_TO_MOD_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or missing X-Service-Key"},
        )


@router.post(
    "/events",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(_verify_b2b_service_key)],
)
async def receive_b2b_product_event(
    body: IncomingB2BEvent,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Приём событий о товарах от B2B (PRODUCT_CREATED, PRODUCT_EDITED, PRODUCT_DELETED)."""
    await handle_b2b_event(db=db, body=body)
