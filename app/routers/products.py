import uuid
from typing import Union

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies.access import ProductAccess, ProductAccessMode, resolve_product_access
from app.dependencies.auth import get_current_seller_id, get_optional_current_seller_id
from app.dependencies.db import get_db
from app.models.product import ProductStatus
from app.schemas.product import (
    B2CProductResponse,
    ProductCreate,
    ProductPaginatedResponse,
    ProductPublicPaginatedResponse,
    ProductResponse,
    ProductShortResponse,
    ProductUpdate,
)
from app.services.catalog_service import list_visible_products
from app.services.product_presenter import (
    product_to_b2c_response,
    product_to_public_response,
    product_to_public_short_response,
    product_to_seller_response,
)
from app.services.product_service import create_product, delete_product, get_product, list_seller_products, update_product

router = APIRouter(prefix="/api/v1/products", tags=["products"])


def _parse_ids_param(ids: str | None) -> list[uuid.UUID] | None:
    if ids is None or not ids.strip():
        return None
    return [uuid.UUID(part.strip()) for part in ids.split(",") if part.strip()]


@router.get(
    "",
    summary="Каталог B2C (X-Service-Key) или список продавца (JWT)",
)
async def list_products_endpoint(
    db: AsyncSession = Depends(get_db),
    x_service_key: str | None = Header(None, alias="X-Service-Key"),
    seller_id: uuid.UUID | None = Depends(get_optional_current_seller_id),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    category: uuid.UUID | None = Query(None, alias="category"),
    search: str | None = Query(None),
    ids: str | None = Query(None, description="Batch: UUID через запятую"),
    status: ProductStatus | None = Query(None),
    include_deleted: bool = Query(False),
) -> ProductPaginatedResponse | ProductPublicPaginatedResponse:
    if x_service_key is not None:
        if x_service_key != settings.B2B_SERVICE_KEY:
            raise HTTPException(
                status_code=http_status.HTTP_401_UNAUTHORIZED,
                detail="Invalid service key",
            )
        return await _handle_b2c_list(db, limit, offset, category, search, ids)

    if seller_id is None:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="Authorization required",
        )

    return await _handle_seller_list(db, seller_id, limit, offset, status, include_deleted, search)


async def _handle_b2c_list(
    db: AsyncSession,
    limit: int,
    offset: int,
    category: uuid.UUID | None,
    search: str | None,
    ids: str | None,
) -> ProductPublicPaginatedResponse:
    product_ids = _parse_ids_param(ids)
    products, total = await list_visible_products(
        db,
        limit=limit,
        offset=offset,
        category_id=category,
        search=search,
        product_ids=product_ids,
    )
    return ProductPublicPaginatedResponse(
        items=[product_to_public_short_response(p) for p in products],
        total_count=total,
        limit=limit,
        offset=offset,
    )


async def _handle_seller_list(
    db: AsyncSession,
    seller_id: uuid.UUID,
    limit: int,
    offset: int,
    status: ProductStatus | None,
    include_deleted: bool,
    search: str | None,
) -> ProductPaginatedResponse:
    products, total = await list_seller_products(
        db,
        seller_id=seller_id,
        limit=limit,
        offset=offset,
        status=status,
        include_deleted=include_deleted,
        search=search,
    )

    items = []
    for p in products:
        prices = [sku.price - sku.discount for sku in p.skus]
        min_price = min(prices) if prices else None

        cover_image = None
        if p.images:
            first = min(p.images, key=lambda img: img.get("ordering", 0))
            cover_image = first.get("url")
        elif p.skus:
            for sku in p.skus:
                if sku.images_rel:
                    cover_image = min(sku.images_rel, key=lambda img: img.ordering).url
                    break

        skus_count = len(p.skus)
        total_active_quantity = sum(
            max(0, sku.stock_quantity - sku.reserved_quantity) for sku in p.skus
        )

        items.append(ProductShortResponse(
            id=p.id,
            title=p.title,
            slug=p.slug,
            status=p.status.value,
            category_id=p.category_id,
            deleted=p.deleted,
            created_at=p.created_at,
            min_price=min_price,
            cover_image=cover_image,
            skus_count=skus_count,
            total_active_quantity=total_active_quantity,
        ))

    return ProductPaginatedResponse(
        items=items,
        total_count=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ProductResponse, status_code=http_status.HTTP_201_CREATED)
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
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Product not found")

    return product_to_b2c_response(product)


@router.put("/{product_id}", response_model=ProductResponse, status_code=http_status.HTTP_200_OK)
async def update_product_endpoint(
    product_id: uuid.UUID,
    body: ProductUpdate,
    seller_id: uuid.UUID = Depends(get_current_seller_id),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse:
    """Обновить товар продавца. HARD_BLOCKED → 403."""
    product = await update_product(db=db, product_id=product_id, data=body, seller_id=seller_id)
    return product_to_seller_response(product)


@router.delete("/{product_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_product_endpoint(
    product_id: uuid.UUID,
    seller_id: uuid.UUID = Depends(get_current_seller_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Удалить товар продавца. HARD_BLOCKED → 403."""
    await delete_product(db=db, product_id=product_id, seller_id=seller_id)