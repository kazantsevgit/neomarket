from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from app.dependencies.auth import get_current_seller_id
from app.dependencies.db import get_db
from app.schemas.product import SKUCreate, SKUResponse, SKUUpdate
from app.services.product_presenter import sku_to_seller_response
from app.services.sku_service import add_sku, delete_sku
from app.services.product_service import update_sku

router = APIRouter(prefix="/api/v1/skus", tags=["skus"])


@router.post("", response_model=SKUResponse, status_code=status.HTTP_201_CREATED)
async def create_sku_endpoint(
    body: SKUCreate,
    seller_id: uuid.UUID = Depends(get_current_seller_id),
    db: AsyncSession = Depends(get_db),
) -> SKUResponse:
    sku = await add_sku(db=db, data=body, seller_id=seller_id)
    return sku_to_seller_response(sku)

@router.put("/{sku_id}", response_model=SKUResponse, status_code=status.HTTP_200_OK)
async def update_sku_endpoint(
    sku_id: uuid.UUID,
    body: SKUUpdate,
    seller_id: uuid.UUID = Depends(get_current_seller_id),
    db: AsyncSession = Depends(get_db),
) -> SKUResponse:
    """
    Редактирование SKU.
    MODERATED/BLOCKED товар → ON_MODERATION + событие EDITED.
    reserved_quantity сохраняется.
    HARD_BLOCKED → 403.
    """
    sku = await update_sku(db=db, sku_id=sku_id, data=body, seller_id=seller_id)
    return sku_to_seller_response(sku)

@router.delete("/{sku_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sku_endpoint(
    sku_id: uuid.UUID,
    seller_id: uuid.UUID = Depends(get_current_seller_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Удаление SKU.
    Guardrail-проверки: HARD_BLOCKED → 403, reserved_quantity > 0 → 409.
    Side-эффекты: DELETED в Moderation (последний SKU), SKU_OUT_OF_STOCK в B2C.
    """
    await delete_sku(db=db, sku_id=sku_id, seller_id=seller_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
