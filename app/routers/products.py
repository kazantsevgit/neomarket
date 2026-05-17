import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_seller_id
from app.dependencies.db import get_db
from app.models.product import Product
from app.schemas.product import (
    CharacteristicResponse,
    ProductCreate,
    ProductImageResponse,
    ProductResponse,
)
from app.services.product_service import create_product

router = APIRouter(prefix="/api/v1/products", tags=["products"])


def _product_to_response(product: Product) -> ProductResponse:
    return ProductResponse(
        id=product.id,
        seller_id=product.seller_id,
        title=product.title,
        slug=product.slug,
        description=product.description,
        category_id=product.category_id,
        status=product.status.value,
        deleted=product.deleted,
        blocking_reason_id=product.blocking_reason_id,
        moderator_comment=product.moderator_comment,
        images=[ProductImageResponse.model_validate(img) for img in product.images],
        characteristics=[
            CharacteristicResponse.model_validate(c) for c in product.characteristics
        ],
        skus=[],
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product_endpoint(
    body: ProductCreate,
    seller_id: uuid.UUID = Depends(get_current_seller_id),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse:
    product = await create_product(db=db, data=body, seller_id=seller_id)
    return _product_to_response(product)
