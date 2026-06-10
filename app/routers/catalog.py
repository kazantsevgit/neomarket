"""
B2C-01: каталог с фильтрами и фасетами.
Покупательские endpoints проксируют B2B public API через X-Service-Key.

B2C-03: карточка товара для покупателя
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.db import get_db
from app.dependencies.filters import parse_catalog_filters_query
from app.models.product import Product, ProductStatus
from app.schemas.catalog import FacetsResponse, ProductShortListResponse
from app.schemas.errors import VALID_SORTS, invalid_sort_error
from app.services import b2b_client
from app.services.product_presenter import product_to_catalog_detail

router = APIRouter(prefix="/api/v1", tags=["Catalog"])


def _validate_sort_param(sort: str | None) -> str | None:
    if sort is None:
        return None
    if sort not in VALID_SORTS:
        raise invalid_sort_error()
    return sort


@router.get("/catalog/products", response_model=ProductShortListResponse)
async def list_products(
    q: str | None = None,
    sort: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    filter: dict | None = Depends(parse_catalog_filters_query),
) -> ProductShortListResponse:
    validated_sort = _validate_sort_param(sort)

    category_id = None
    min_price = None
    max_price = None
    filters = None
    if filter:
        if "category_id" in filter:
            category_id = uuid.UUID(filter["category_id"])
        if "price_min" in filter:
            min_price = int(filter["price_min"])
        if "price_max" in filter:
            max_price = int(filter["max_price"])
        known = {"category_id", "price_min", "price_max", "seller_id"}
        dyn_filters = {k: v for k, v in filter.items() if k not in known}
        if dyn_filters:
            filters = dyn_filters

    return await b2b_client.list_products(
        category_id=category_id,
        search=q,
        filters=filters,
        sort=validated_sort,
        limit=limit,
        offset=offset,
        min_price=min_price,
        max_price=max_price,
    )


@router.get("/catalog/facets", response_model=FacetsResponse)
async def get_catalog_facets(
    category_id: uuid.UUID | None = None,
    q: str | None = None,
    filter: dict | None = Depends(parse_catalog_filters_query),
) -> FacetsResponse:
    filters = None
    if filter:
        known = {"category_id", "price_min", "price_max", "seller_id"}
        dyn_filters = {k: v for k, v in filter.items() if k not in known}
        if dyn_filters:
            filters = dyn_filters
    return await b2b_client.get_facets(
        category_id=category_id,
        search=q,
        filters=filters,
    )


@router.get("/catalog/products/{product_id}", summary="Карточка товара (публичная)")
async def get_catalog_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(Product, product_id)
    if product is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"code": "NOT_FOUND", "message": "Product not found"},
        )

    if product.deleted or product.status != ProductStatus.MODERATED:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"code": "NOT_FOUND", "message": "Product not found"},
        )

    return product_to_catalog_detail(product)