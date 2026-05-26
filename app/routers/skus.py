from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from app.dependencies.auth import get_current_seller_id
from app.dependencies.db import get_db
from app.schemas.product import SKUCreate, SKUResponse
from app.services.product_presenter import sku_to_seller_response
from app.services.sku_service import add_sku

router = APIRouter(prefix="/api/v1/skus", tags=["skus"])


@router.post("", response_model=SKUResponse, status_code=status.HTTP_201_CREATED)
async def create_sku_endpoint(
    body: SKUCreate,
    seller_id: uuid.UUID = Depends(get_current_seller_id),
    db: AsyncSession = Depends(get_db),
) -> SKUResponse:
    sku = await add_sku(db=db, data=body, seller_id=seller_id)
    return sku_to_seller_response(sku)