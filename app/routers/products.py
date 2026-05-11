import uuid
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_seller_id
from app.dependencies.db import get_db
from app.schemas.product import ProductCreate, ProductResponse
from app.services.product_service import create_product

router = APIRouter(prefix="/api/v1/products", tags=["products"])


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product_endpoint(
    body: ProductCreate,
    seller_id: uuid.UUID = Depends(get_current_seller_id),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse:
    product = await create_product(db=db, data=body, seller_id=seller_id)
    return ProductResponse.model_validate({**product.__dict__, "skus": []})