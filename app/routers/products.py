import uuid
from typing import Union

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.access import ProductAccess, ProductAccessMode, resolve_product_access
from app.dependencies.auth import get_current_seller_id
from app.dependencies.db import get_db
from app.schemas.product import ProductCreate, ProductPublicResponse, ProductResponse
from app.services.product_presenter import product_to_public_response, product_to_seller_response
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
    response_model=Union[ProductResponse, ProductPublicResponse],
    summary="Карточка товара (seller — полная, X-Service-Key — без cost_price/reserved_quantity)",
)
async def get_product_endpoint(
    product_id: uuid.UUID,
    access: ProductAccess = Depends(resolve_product_access),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse | ProductPublicResponse:
    seller_id = access.seller_id if access.mode == ProductAccessMode.SELLER else None
    product = await get_product(db=db, product_id=product_id, seller_id=seller_id)

    if access.mode == ProductAccessMode.SELLER:
        return product_to_seller_response(product)
    return product_to_public_response(product)
