from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies.db import get_db
from app.schemas.b2b_event import B2BProductEventRequest
from app.services.b2b_event_service import handle_b2b_event

router = APIRouter(prefix="/api/v1/events", tags=["b2b_events"])


def _verify_b2b_service_key(
    x_service_key: str | None = Header(None, alias="X-Service-Key"),
) -> None:
    if x_service_key is None or x_service_key != settings.B2B_TO_MOD_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or missing X-Service-Key"},
        )


@router.post(
    "/product",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(_verify_b2b_service_key)],
)
async def receive_b2b_product_event(
    body: B2BProductEventRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Приём событий о товарах от B2B (CREATED, EDITED, DELETED)."""
    await handle_b2b_event(db=db, body=body)
    return {"status": "ok"}
