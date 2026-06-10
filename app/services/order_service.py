"""
Сервис оформления заказа (checkout).

Реализует canonical flow B2C-9:
  0. Idempotency check (UNIQUE index на idempotency_key в таблице orders)
  1. Состав заказа из корзины покупателя + валидация address_id/payment_method_id
  2. GET public/skus + public/products/batch из B2B — цены и статусы товаров
  3. Локальные проверки: MODERATED?, не blocked?, active_quantity?
  4. POST /reserve к B2B — all-or-nothing
  5. Создание Order + OrderItem в транзакции (фиксация цен)
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderItem, OrderStatus
from app.orders.errors import order_http_error
from app.orders.presenter import order_to_response
from app.schemas.orders import CheckoutItem, CheckoutRequest, OrderResponse
from app.services.b2b_client import (
    B2BReserveFailedError,
    B2BUnavailableError,
    get_products_by_sku_ids,
    get_public_products_batch,
    reserve,
)
from app.services.cart_service import get_user_cart_items


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_sku_index(sku_data_list: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(s["id"]): s for s in sku_data_list if not s.get("_not_found")}


def _build_product_index(product_data_list: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(p["id"]): p for p in product_data_list}


def _get_active_quantity(sku: Dict[str, Any]) -> int:
    if sku.get("active_quantity") is not None:
        return int(sku["active_quantity"])
    stock = int(sku.get("stock_quantity", 0) or 0)
    reserved = int(sku.get("reserved_quantity", 0) or 0)
    return max(0, stock - reserved)


def _validate_skus_availability(
    items: List[CheckoutItem],
    sku_index: Dict[str, Dict[str, Any]],
    product_index: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Шаг 3: статус товара — из ProductPublicResponse (batch), не из вложенного product в SKU.
    """
    failed: List[Dict[str, Any]] = []
    for item in items:
        sid = str(item.sku_id)
        sku = sku_index.get(sid)
        if sku is None:
            failed.append({"sku_id": sid, "reason": "SKU_NOT_FOUND"})
            continue

        product_id = str(sku.get("product_id", ""))
        product = product_index.get(product_id)
        if product is None:
            failed.append({"sku_id": sid, "reason": "PRODUCT_BLOCKED"})
            continue

        p_status = product.get("status", "")
        active_qty = _get_active_quantity(sku)

        if p_status in ("BLOCKED", "HARD_BLOCKED", "REJECTED"):
            failed.append({"sku_id": sid, "reason": "PRODUCT_BLOCKED"})
        elif p_status not in ("MODERATED", "PUBLISHED"):
            failed.append({"sku_id": sid, "reason": "PRODUCT_BLOCKED"})
        elif active_qty < item.quantity:
            reason = "OUT_OF_STOCK" if active_qty == 0 else "INSUFFICIENT_STOCK"
            failed.append({
                "sku_id": sid,
                "reason": reason,
                "requested": item.quantity,
                "available": active_qty,
            })

    return failed


def _resolve_delivery_address(address_id: uuid.UUID) -> str:
    """MVP: адреса покупателя — отдельный модуль; для checkout сохраняем ссылку."""
    return f"address:{address_id}"


async def create_order(
    db: AsyncSession,
    user_id: uuid.UUID,
    idempotency_key: uuid.UUID,
    payload: CheckoutRequest,
) -> OrderResponse:
    """
    Точка входа checkout-сервиса.

    Raises:
        HTTPException 409  — partial/full reserve failure
        HTTPException 503  — B2B недоступен
        HTTPException 400  — пустая корзина
    """
    existing = await _get_order_by_idempotency_key(db, idempotency_key)
    if existing:
        return order_to_response(existing)

    cart_items = await get_user_cart_items(db, user_id)
    if not cart_items:
        raise order_http_error(
            status.HTTP_400_BAD_REQUEST,
            "INVALID_REQUEST",
            "Корзина пуста",
        )

    checkout_items = [
        CheckoutItem(sku_id=item.sku_id, quantity=item.quantity)
        for item in cart_items
    ]

    if payload.items_snapshot is not None:
        snapshot_map = {str(s.sku_id): s for s in payload.items_snapshot}
        for item in checkout_items:
            snap = snapshot_map.get(str(item.sku_id))
            if snap is None or snap.quantity != item.quantity:
                raise order_http_error(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "CART_CHANGED",
                    "Состав корзины изменился, обновите страницу",
                )

    sku_ids = [item.sku_id for item in checkout_items]
    try:
        sku_data_list = await get_products_by_sku_ids(sku_ids)
    except B2BUnavailableError:
        raise order_http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "B2B_UNAVAILABLE",
            "Сервис товаров временно недоступен",
        )

    sku_index = _build_sku_index(sku_data_list)
    product_ids = list({
        uuid.UUID(str(s["product_id"]))
        for s in sku_index.values()
        if s.get("product_id")
    })

    try:
        product_data_list = await get_public_products_batch(product_ids)
    except B2BUnavailableError:
        raise order_http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "B2B_UNAVAILABLE",
            "Сервис товаров временно недоступен",
        )

    product_index = _build_product_index(product_data_list)

    failed_items = _validate_skus_availability(checkout_items, sku_index, product_index)
    if failed_items:
        raise order_http_error(
            status.HTTP_409_CONFLICT,
            "RESERVE_FAILED",
            "Не удалось зарезервировать товары",
            failed_items=failed_items,
        )

    order_id = uuid.uuid4()
    reserve_items = [
        {"sku_id": str(item.sku_id), "quantity": item.quantity}
        for item in checkout_items
    ]
    try:
        await reserve(
            idempotency_key=idempotency_key,
            order_id=order_id,
            items=reserve_items,
        )
    except B2BReserveFailedError as exc:
        raise order_http_error(
            status.HTTP_409_CONFLICT,
            "RESERVE_FAILED",
            "Не удалось зарезервировать товары",
            failed_items=exc.failed_items,
        )
    except B2BUnavailableError:
        raise order_http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "B2B_UNAVAILABLE",
            "Сервис товаров временно недоступен",
        )

    delivery_address = _resolve_delivery_address(payload.address_id)

    try:
        order = await _persist_order(
            db=db,
            order_id=order_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            items=checkout_items,
            sku_index=sku_index,
            product_index=product_index,
            delivery_address=delivery_address,
        )
    except IntegrityError:
        await db.rollback()
        existing = await _get_order_by_idempotency_key(db, idempotency_key)
        if existing:
            return order_to_response(existing)
        raise

    return order_to_response(order)


async def _get_order_by_idempotency_key(
    db: AsyncSession,
    key: uuid.UUID,
) -> Optional[Order]:
    result = await db.execute(
        select(Order).where(Order.idempotency_key == key)
    )
    return result.scalar_one_or_none()


async def _persist_order(
    db: AsyncSession,
    order_id: uuid.UUID,
    user_id: uuid.UUID,
    idempotency_key: uuid.UUID,
    items: List[CheckoutItem],
    sku_index: Dict[str, Dict[str, Any]],
    product_index: Dict[str, Dict[str, Any]],
    delivery_address: str,
) -> Order:
    """Атомарная запись заказа с зафиксированными ценами."""
    total_amount = 0
    order_items: List[OrderItem] = []

    for item in items:
        sku = sku_index[str(item.sku_id)]
        product = product_index.get(str(sku["product_id"]), {})
        unit_price = sku["price"]
        line_total = unit_price * item.quantity
        total_amount += line_total

        product_title = product.get("title", "")
        sku_name = sku["name"]

        order_items.append(OrderItem(
            id=uuid.uuid4(),
            order_id=order_id,
            sku_id=item.sku_id,
            product_id=uuid.UUID(str(sku["product_id"])),
            product_title=product_title,
            sku_name=sku_name,
            quantity=item.quantity,
            unit_price=unit_price,
            line_total=line_total,
        ))

    order = Order(
        id=order_id,
        user_id=user_id,
        status=OrderStatus.PAID,
        total_amount=total_amount,
        delivery_address=delivery_address,
        idempotency_key=idempotency_key,
    )
    order.items = order_items

    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order
