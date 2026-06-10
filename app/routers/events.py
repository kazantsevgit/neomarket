from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies.db import get_db
from app.schemas.events import ProductEventRequest, ProductEventResponse
from app.services.product_event_service import process_product_event

router = APIRouter(prefix="/api/v1/events", tags=["events"])


def _verify_service_key(x_service_key: str | None = Header(None, alias="X-Service-Key")) -> None:
    if x_service_key is None or x_service_key != settings.B2B_SERVICE_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or missing X-Service-Key"},
        )


@router.post(
    "/product",
    response_model=ProductEventResponse,
    dependencies=[Depends(_verify_service_key)],
)
async def receive_product_event(
    body: ProductEventRequest,
    db: AsyncSession = Depends(get_db),
) -> ProductEventResponse:
    """Приём событий PRODUCT_BLOCKED / PRODUCT_DELETED / SKU_OUT_OF_STOCK от B2B."""
    await process_product_event(db=db, payload=body)
    return ProductEventResponse(accepted=True)
