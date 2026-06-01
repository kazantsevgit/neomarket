import uuid
from typing import Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.access import ProductAccess, ProductAccessMode, resolve_product_access
from app.dependencies.auth import get_current_seller_id
from app.dependencies.db import get_db
from app.dependencies.service_key import require_catalog_service_key
from app.models.product import ProductStatus
from app.schemas.product import (
    B2CProductResponse,
    ProductCreate,
    ProductPublicPaginatedResponse,
    ProductResponse,
    ProductUpdate,
)
from app.services.catalog_service import list_catalog_products
from app.services.product_presenter import (
    product_to_b2c_response,
    product_to_public_response,
    product_to_seller_response,
)
from app.services.product_service import create_product, delete_product, get_product, update_product

router = APIRouter(prefix="/api/v1/products", tags=["products"])


def _parse_ids_param(ids: str | None) -> list[uuid.UUID] | None:
    if ids is None or not ids.strip():
        return None
    return [uuid.UUID(part.strip()) for part in ids.split(",") if part.strip()]


@router.get(
    "",
    response_model=ProductPublicPaginatedResponse,
    summary="Каталог B2C (X-Service-Key) или список продавца (JWT)",
)
async def list_products_endpoint(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_catalog_service_key),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    category: uuid.UUID | None = Query(None, alias="category"),
    search: str | None = Query(None, min_length=3),
    ids: str | None = Query(None, description="Batch: UUID через запятую"),
) -> ProductPublicPaginatedResponse:
    product_ids = _parse_ids_param(ids)
    products, total = await list_catalog_products(
        db,
        limit=limit,
        offset=offset,
        category_id=category,
        search=search,
        product_ids=product_ids,
    )
    return ProductPublicPaginatedResponse(
        items=[product_to_public_response(p) for p in products],
        total_count=total,
        limit=limit,
        offset=offset,
    )


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

    if product.deleted or product.status != ProductStatus.MODERATED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    return product_to_b2c_response(product)


@router.put("/{product_id}", response_model=ProductResponse, status_code=status.HTTP_200_OK)
async def update_product_endpoint(
    product_id: uuid.UUID,
    body: ProductUpdate,
    seller_id: uuid.UUID = Depends(get_current_seller_id),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse:
    """Обновить товар продавца. HARD_BLOCKED → 403."""
    product = await update_product(db=db, product_id=product_id, data=body, seller_id=seller_id)
    return product_to_seller_response(product)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product_endpoint(
    product_id: uuid.UUID,
    seller_id: uuid.UUID = Depends(get_current_seller_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Удалить товар продавца. HARD_BLOCKED → 403."""
    await delete_product(db=db, product_id=product_id, seller_id=seller_id)
