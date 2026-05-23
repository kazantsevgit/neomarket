"""
B2C-1: каталог с фильтрами и фасетами.
Покупательские endpoints проксируют B2B public API через X-Service-Key.
"""

import uuid

from fastapi import APIRouter, Depends, Query

from app.dependencies.filters import parse_filters_query
from app.schemas.catalog import FacetsResponse, ProductShortListResponse
from app.schemas.errors import VALID_SORTS, invalid_sort_error
from app.services import b2b_client

router = APIRouter(prefix="/api/v1", tags=["Catalog"])


def _validate_sort_param(sort: str | None) -> str | None:
    if sort is None:
        return None
    if sort not in VALID_SORTS:
        raise invalid_sort_error()
    return sort


@router.get("/products", response_model=ProductShortListResponse)
async def list_products(
    category_id: uuid.UUID | None = None,
    search: str | None = None,
    sort: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    filters: dict | None = Depends(parse_filters_query),
) -> ProductShortListResponse:
    validated_sort = _validate_sort_param(sort)
    return await b2b_client.list_products(
        category_id=category_id,
        search=search,
        filters=filters,
        sort=validated_sort,
        limit=limit,
        offset=offset,
    )


@router.get("/catalog/facets", response_model=FacetsResponse)
async def get_catalog_facets(
    category_id: uuid.UUID | None = None,
    search: str | None = None,
    filters: dict | None = Depends(parse_filters_query),
) -> FacetsResponse:
    return await b2b_client.get_facets(
        category_id=category_id,
        search=search,
        filters=filters,
    )
