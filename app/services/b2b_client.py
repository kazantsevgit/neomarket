"""
HTTP-клиент B2C → B2B.

Используется в checkout:
  - get_products_by_sku_ids() — проверка наличия и получение цен/названий
  - reserve()                 — all-or-nothing резервирование

Все вызовы передают X-Service-Key (межсервисная аутентификация).
При недоступности B2B (ConnectionError, таймаут, 5xx) клиент бросает
B2BUnavailableError — B2C роутер обрабатывает его как 503.
"""

import uuid
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings


class B2BUnavailableError(Exception):
    """B2B-сервис недоступен (таймаут / 5xx)."""


class B2BReserveFailedError(Exception):
    """B2B вернул 409 — не удалось зарезервировать."""

    def __init__(self, failed_items: List[Dict[str, Any]]) -> None:
        super().__init__("reserve failed")
        self.failed_items = failed_items


def _b2b_headers() -> Dict[str, str]:
    return {
        "X-Service-Key": settings.B2B_SERVICE_KEY,
        "Content-Type": "application/json",
    }


async def get_products_by_sku_ids(sku_ids: List[uuid.UUID]) -> List[Dict[str, Any]]:
    """
    GET /api/v1/public/products/batch — batch-запрос по sku_ids.

    B2B не поддерживает фильтр по sku_id напрямую, поэтому используем
    product_ids, которые мы не знаем заранее. Вместо этого делаем отдельные
    запросы через GET /api/v1/public/skus/{sku_id} для каждого SKU.

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
                    # SKU не найден — добавим маркер для дальнейшей проверки
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
        # B2B возвращает detail.sku_ids — приводим к формату failed_items B2C
        failed_items = detail.get("failed_items", [])
        if not failed_items:
            sku_ids = detail.get("sku_ids", [])
            failed_items = [
                {"sku_id": sid, "reason": "INSUFFICIENT_STOCK"}
                for sid in sku_ids
            ]
        raise B2BReserveFailedError(failed_items)

    raise B2BUnavailableError(f"B2B returned {resp.status_code}")
