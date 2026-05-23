"""HTTP-клиент B2C → B2B для каталога (проксирование public endpoints)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from fastapi import HTTPException

from app.config import settings
from app.schemas.catalog import FacetsResponse, ProductShortListResponse
from app.schemas.errors import b2b_unavailable_error

_PUBLIC_PRODUCTS = "/api/v1/public/products"
_PUBLIC_FACETS = "/api/v1/public/catalog/facets"


def _headers() -> dict[str, str]:
    return {"X-Service-Key": settings.B2B_SERVICE_KEY}


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code == 400:
        raise HTTPException(status_code=400, detail=resp.json().get("detail", resp.json()))
    if resp.status_code >= 500:
        raise b2b_unavailable_error()
    resp.raise_for_status()


async def list_products(
    *,
    category_id: uuid.UUID | None = None,
    search: str | None = None,
    filters: dict[str, Any] | None = None,
    sort: str | None = None,
    limit: int = 20,
    offset: int = 0,
    min_price: int | None = None,
    max_price: int | None = None,
) -> ProductShortListResponse:
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if category_id is not None:
        params["category_id"] = str(category_id)
    if search is not None:
        params["search"] = search
    if sort is not None:
        params["sort"] = sort
    if min_price is not None:
        params["min_price"] = min_price
    if max_price is not None:
        params["max_price"] = max_price
    if filters:
        for key, value in filters.items():
            if isinstance(value, list):
                for item in value:
                    params[f"filters[{key}]"] = item
            else:
                params[f"filters[{key}]"] = value

    try:
        async with httpx.AsyncClient(
            base_url=settings.B2B_BASE_URL,
            timeout=settings.B2B_HTTP_TIMEOUT,
        ) as client:
            resp = await client.get(_PUBLIC_PRODUCTS, params=params, headers=_headers())
    except (httpx.RequestError, httpx.TimeoutException):
        raise b2b_unavailable_error() from None

    _raise_for_status(resp)
    return ProductShortListResponse.model_validate(resp.json())


async def get_facets(
    *,
    category_id: uuid.UUID | None = None,
    search: str | None = None,
    filters: dict[str, Any] | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
) -> FacetsResponse:
    params: dict[str, Any] = {}
    if category_id is not None:
        params["category_id"] = str(category_id)
    if search is not None:
        params["search"] = search
    if min_price is not None:
        params["min_price"] = min_price
    if max_price is not None:
        params["max_price"] = max_price
    if filters:
        for key, value in filters.items():
            if isinstance(value, list):
                for item in value:
                    params[f"filters[{key}]"] = item
            else:
                params[f"filters[{key}]"] = value

    try:
        async with httpx.AsyncClient(
            base_url=settings.B2B_BASE_URL,
            timeout=settings.B2B_HTTP_TIMEOUT,
        ) as client:
            resp = await client.get(_PUBLIC_FACETS, params=params, headers=_headers())
    except (httpx.RequestError, httpx.TimeoutException):
        raise b2b_unavailable_error() from None

    _raise_for_status(resp)
    return FacetsResponse.model_validate(resp.json())
