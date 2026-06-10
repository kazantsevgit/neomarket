"""
HTTP-клиент B2C → B2B для каталога и инвентаря (проксирование public endpoints + резервирование).

Включает:
- функции для работы с каталогом (list_products, get_facets) из ветки b2c-catalog-b2c-1
- функции для работы с инвентарём (get_products_by_sku_ids, reserve, unreserve) из ветки main
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from app.config import settings
from app.schemas.catalog import FacetsResponse, ProductShortListResponse
from app.schemas.errors import b2b_unavailable_error



class B2BUnavailableError(Exception):
    """B2B-сервис недоступен (таймаут / 5xx)."""


class B2BReserveFailedError(Exception):
    """B2B вернул 409 — не удалось зарезервировать."""

    def __init__(self, failed_items: List[Dict[str, Any]]) -> None:
        super().__init__("reserve failed")
        self.failed_items = failed_items

# b2c-catalog-b2c-1
def _headers() -> dict[str, str]:
    """Заголовки для запросов к B2B (используется в каталоге)."""
    return {"X-Service-Key": settings.B2B_SERVICE_KEY}


def _b2b_headers() -> Dict[str, str]:
    """Заголовки для запросов к B2B (используется в инвентаре)."""
    return {
        "X-Service-Key": settings.B2B_SERVICE_KEY,
        "Content-Type": "application/json",
    }


# b2c-catalog-b2c-1
def _raise_for_status(resp: httpx.Response) -> None:
    """Обработка HTTP-статусов для каталога."""
    if resp.status_code == 400:
        raise HTTPException(status_code=400, detail=resp.json().get("detail", resp.json()))
    if resp.status_code >= 500:
        raise b2b_unavailable_error()
    resp.raise_for_status()


# b2c-catalog-b2c-1
_PUBLIC_PRODUCTS = "/api/v1/public/products"
_PUBLIC_FACETS = "/api/v1/public/catalog/facets"


# b2c-catalog-b2c-1
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


# b2c-catalog-b2c-1
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


async def get_products_by_sku_ids(sku_ids: List[uuid.UUID]) -> List[Dict[str, Any]]:
    """
    GET /api/v1/public/skus/{sku_id} для каждого SKU.

    Возвращает список dict с полями: id (sku), product_id, name (sku_name),
    price, product (вложенный dict с title, status, deleted).
    """
    results: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for sku_id in sku_ids:
                resp = await client.get(
                    f"{settings.B2B_URL}/api/v1/public/skus/{sku_id}",
                    headers=_b2b_headers(),
                )
                if resp.status_code == 200:
                    results.append(resp.json())
                elif resp.status_code == 404:
                    results.append({"id": str(sku_id), "_not_found": True})
                else:
                    raise B2BUnavailableError(
                        f"B2B returned {resp.status_code} for sku {sku_id}"
                    )
    except httpx.RequestError as exc:
        raise B2BUnavailableError(str(exc)) from exc
    return results


async def reserve(
    idempotency_key: uuid.UUID,
    order_id: uuid.UUID,
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    POST /api/v1/inventory/reserve — all-or-nothing резервирование.

    Возвращает словарь с order_id, status, reserved_at при успехе.
    Бросает B2BReserveFailedError(failed_items) при 409.
    Бросает B2BUnavailableError при сетевых проблемах или 5xx.
    """
    payload = {
        "idempotency_key": str(idempotency_key),
        "order_id": str(order_id),
        "items": items,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.B2B_URL}/api/v1/inventory/reserve",
                json=payload,
                headers=_b2b_headers(),
            )
    except httpx.RequestError as exc:
        raise B2BUnavailableError(str(exc)) from exc

    if resp.status_code == 200:
        return resp.json()

    if resp.status_code == 409:
        detail = resp.json().get("detail", {})
        failed_items = detail.get("failed_items", [])
        if not failed_items:
            sku_ids = detail.get("sku_ids", [])
            failed_items = [
                {"sku_id": sid, "reason": "INSUFFICIENT_STOCK"}
                for sid in sku_ids
            ]
        raise B2BReserveFailedError(failed_items)

    raise B2BUnavailableError(f"B2B returned {resp.status_code}")


async def fetch_product_from_b2b(product_id: uuid.UUID) -> dict | None:
    """
    GET {b2b_url}/api/v1/products/{product_id} от имени Moderation.

    Возвращает dict с данными товара или None при 404.
    Бросает B2BUnavailableError при сетевых проблемах или 5xx.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.B2B_URL}/api/v1/products/{product_id}",
                headers={"X-Service-Key": settings.MOD_TO_B2B_KEY},
            )
    except httpx.RequestError as exc:
        raise B2BUnavailableError(str(exc)) from exc

    if resp.status_code == 404:
        return None
    if resp.status_code >= 500:
        raise B2BUnavailableError(f"B2B returned {resp.status_code}")
    resp.raise_for_status()
    return resp.json()


async def unreserve(
    order_id: uuid.UUID,
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    POST /api/v1/inventory/unreserve — снять резерв при отмене заказа.

    Идемпотентен на стороне B2B по order_id.
    Бросает B2BUnavailableError при таймауте или 5xx.
    """
    payload = {
        "order_id": str(order_id),
        "items": items,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.B2B_URL}/api/v1/inventory/unreserve",
                json=payload,
                headers=_b2b_headers(),
            )
    except httpx.RequestError as exc:
        raise B2BUnavailableError(str(exc)) from exc

    if resp.status_code == 200:
        return resp.json()

    raise B2BUnavailableError(f"B2B returned {resp.status_code}")