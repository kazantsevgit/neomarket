"""
Сервис доставки заказа (fulfill при DELIVERED).

Реализует canonical flow B2C-13:
  1. Загружаем заказ — проверка статуса (только DELIVERING → DELIVERED).
  2. POST /api/v1/inventory/fulfill → B2B.
     - Успех → статус DELIVERED.
     - Таймаут / 5xx → заказ уже DELIVERED, товар у покупателя.
       reserved_quantity завышен до успешного retry.
       Scaffold: лог ошибки, async retry в следующей итерации.
  3. Сохраняем заказ, возвращаем покупателю.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderStatus
from app.orders.errors import order_http_error
from app.orders.presenter import order_to_response
from app.schemas.orders import OrderResponse
from app.services.b2b_client import B2BUnavailableError, fulfill

logger = logging.getLogger(__name__)

_DELIVERABLE = {OrderStatus.DELIVERING}


async def deliver_order(
    db: AsyncSession,
    order_id: uuid.UUID,
) -> OrderResponse:
    """
    Отметить заказ как доставленный.

    Вызывает POST /api/v1/inventory/fulfill → B2B для списания резерва.
    При падении B2B заказ остаётся DELIVERED — товар уже у покупателя.
    Retry асинхронный (scaffold: лог ошибки).

    Raises:
        HTTPException 404 — заказ не найден
        HTTPException 409 — заказ в статусе, который нельзя доставить
    """
    # ── 1. Загрузка заказа ──────────────────────────────────────────────────
    result = await db.execute(select(Order).where(Order.id == order_id))
    order: Order | None = result.scalar_one_or_none()

    if order is None:
        raise order_http_error(
            status.HTTP_404_NOT_FOUND,
            "ORDER_NOT_FOUND",
            "Заказ не найден",
        )

    # ── 2. Проверка допустимого статуса ──────────────────────────────────────
    if order.status not in _DELIVERABLE:
        raise order_http_error(
            status.HTTP_409_CONFLICT,
            "DELIVER_NOT_ALLOWED",
            f"Доставка невозможна: заказ в статусе {order.status.value}",
            current_status=order.status.value,
        )

    # ── 3. POST /fulfill → B2B ─────────────────────────────────────────────
    fulfill_items: List[Dict[str, Any]] = [
        {"sku_id": str(item.sku_id), "quantity": item.quantity}
        for item in order.items
    ]

    try:
        await fulfill(order_id=order.id, items=fulfill_items)
    except B2BUnavailableError as exc:
        logger.error(
            "fulfill failed for order %s (status=DELIVERED), async retry needed: %s",
            order_id,
            exc,
        )

    # Статус меняется независимо от результата fulfill
    order.status = OrderStatus.DELIVERED
    order.updated_at = datetime.now(timezone.utc)

    # ── 4. Сохраняем ────────────────────────────────────────────────────────
    await db.commit()
    await db.refresh(order)

    return order_to_response(order)
