from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies.db import get_db
from app.schemas.inventory import (
    InventoryOrderRequest,
    InventoryOrderResponse,
    ReserveRequest,
    ReserveResponse,
)
from app.services.inventory_service import reserve_inventory, unreserve_inventory

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])


def _verify_service_key(x_service_key: str = Header(..., alias="X-Service-Key")) -> None:
    """Простая проверка ключа межсервисного взаимодействия."""
    if x_service_key != settings.SERVICE_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Service-Key",
        )


@router.post(
    "/reserve",
    response_model=ReserveResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(_verify_service_key)],
)
async def reserve_endpoint(
    body: ReserveRequest,
    db: AsyncSession = Depends(get_db),
) -> ReserveResponse:
    """All-or-nothing резервирование SKU (вызывается B2C при checkout)."""
    return await reserve_inventory(db=db, payload=body)


@router.post(
    "/unreserve",
    response_model=InventoryOrderResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(_verify_service_key)],
)
async def unreserve_endpoint(
    body: InventoryOrderRequest,
    db: AsyncSession = Depends(get_db),
) -> InventoryOrderResponse:
    """Снять резерв при отмене заказа."""
    return await unreserve_inventory(db=db, order_id=body.order_id, items=body.items)