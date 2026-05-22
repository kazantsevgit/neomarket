import uuid
from typing import Union

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.access import ProductAccess, ProductAccessMode, resolve_product_access
from app.dependencies.auth import get_current_seller_id
from app.dependencies.db import get_db
from app.models.product import ProductStatus
from app.schemas.product import B2CProductResponse, ProductCreate, ProductResponse
from app.services.product_presenter import product_to_b2c_response, product_to_seller_response
from app.services.product_service import create_product, get_product

router = APIRouter(prefix="/api/v1/products", tags=["products"])


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product_endpoint(
    body: ProductCreate,
    seller_id: uuid.UUID = Depends(get_current_seller_id),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse:
    product = await create_product(db=db, data=body, seller_id=seller_id)
    return product_to_seller_response(product)


@router.get(
    "/{product_id}",
    response_model=Union[ProductResponse, B2CProductResponse],
    summary="Карточка товара (seller — полная, B2C — только buyer-поля)",
)
async def get_product_endpoint(
    product_id: uuid.UUID,
    access: ProductAccess = Depends(resolve_product_access),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse | B2CProductResponse:
    if access.mode == ProductAccessMode.SELLER:
        product = await get_product(db=db, product_id=product_id, seller_id=access.seller_id)
        return product_to_seller_response(product)

    # B2C / service access — only visible products
    product = await get_product(db=db, product_id=product_id)

    if product.deleted or product.status not in (ProductStatus.MODERATED, ProductStatus.PUBLISHED):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    return product_to_b2c_response(product)
