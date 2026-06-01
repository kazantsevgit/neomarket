import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies.db import get_db
from app.dependencies.filters import parse_filters_query
from app.schemas.catalog import FacetsResponse, ProductShortListResponse
from app.services import catalog_service

router = APIRouter(prefix="/api/v1/public", tags=["Public Catalog"])


def _verify_service_key(x_service_key: str = Header(..., alias="X-Service-Key")) -> None:
    if x_service_key != settings.B2B_SERVICE_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Service-Key",
        )


@router.get(
    "/products",
    response_model=ProductShortListResponse,
    dependencies=[Depends(_verify_service_key)],
)
async def list_public_products(
    category_id: uuid.UUID | None = None,
    search: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    sort: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    filters: dict | None = Depends(parse_filters_query),
    db: AsyncSession = Depends(get_db),
) -> ProductShortListResponse:
    return await catalog_service.list_catalog_products(
        db,
        category_id=category_id,
        search=search,
        filters=filters,
        sort=sort,
        limit=limit,
        offset=offset,
        min_price=min_price,
        max_price=max_price,
    )


@router.get(
    "/catalog/facets",
    response_model=FacetsResponse,
    dependencies=[Depends(_verify_service_key)],
)
async def get_public_facets(
    category_id: uuid.UUID | None = None,
    search: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    filters: dict | None = Depends(parse_filters_query),
    db: AsyncSession = Depends(get_db),
) -> FacetsResponse:
    return await catalog_service.get_catalog_facets(
        db,
        category_id=category_id,
        search=search,
        filters=filters,
        min_price=min_price,
        max_price=max_price,
    )
