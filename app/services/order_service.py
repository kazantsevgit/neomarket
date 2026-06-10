"""
Сервис оформления заказа (checkout).

Реализует canonical flow B2C-9:
  0. Idempotency check (UNIQUE index на idempotency_key в таблице orders)
  1. Валидация items (непустой список, quantity >= 1 — в схеме)
  2. GET products/skus из B2B — проверка статусов и получение цен
  3. Локальные проверки: MODERATED?, не deleted/blocked?, active_quantity?
  4. POST /reserve к B2B — all-or-nothing
  5. Создание Order + OrderItem в транзакции (фиксация цен)

ADR (выбор механизма идемпотентности):
  Рассматривались три варианта:
  1. UNIQUE index на idempotency_key в таблице orders — при гонке (два одновременных
     запроса с одним ключом) второй получит IntegrityError от БД; код перехватывает
     его и возвращает существующий заказ. Сложность минимальная, нет доп. таблиц.
  2. Отдельная таблица-кэш ключей — явная история, TTL управляется отдельно,
     но требует дополнительной таблицы и двух INSERT в транзакции.
  3. Redis — очень быстрая проверка, но инфраструктурная зависимость; при рестарте
     Redis идемпотентность ломается.

  Выбран вариант 1: UNIQUE index в таблице orders.
  Критерии:
  - Race condition: IntegrityError при конкурентной вставке → поднимаем lookup
    и возвращаем существующий заказ — не дублируем резервирование.
  - Сложность: нулевая — один UniqueConstraint, перехват исключения в сервисе.
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
    reserve,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_sku_index(sku_data_list: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Строит индекс sku_id (str) → sku_dict для быстрого поиска."""
    return {str(s["id"]): s for s in sku_data_list if not s.get("_not_found")}


def _validate_skus_availability(
    items: List[CheckoutItem],
    sku_index: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Шаг 3 из canonical flow: проверяем статус каждого SKU/Product.
    Возвращает failed_items (может быть пустым).
    """
    failed: List[Dict[str, Any]] = []
    for item in items:
        sid = str(item.sku_id)
        sku = sku_index.get(sid)
        if sku is None:
            failed.append({"sku_id": sid, "reason": "SKU_NOT_FOUND"})
            continue

        product = sku.get("product") or {}
        p_status = product.get("status", "")
        p_deleted = product.get("deleted", False)
        active_qty = sku.get("active_quantity", 0)

        if p_deleted:
            failed.append({"sku_id": sid, "reason": "PRODUCT_DELETED"})
        elif p_status == "BLOCKED" or p_status == "HARD_BLOCKED":
            failed.append({"sku_id": sid, "reason": "PRODUCT_BLOCKED"})
        elif p_status != "MODERATED":
            # Не prошёл модерацию
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


async def create_order(
    db: AsyncSession,
    user_id: uuid.UUID,
    payload: CheckoutRequest,
) -> OrderResponse:
    """
    Точка входа checkout-сервиса.

    Raises:
        HTTPException 409  — partial/full reserve failure
        HTTPException 503  — B2B недоступен
        HTTPException 400  — пустой items (защита, основная — в схеме)
    """
    # ── 0. Idempotency check ──────────────────────────────────────────────────
    existing = await _get_order_by_idempotency_key(db, payload.idempotency_key)
    if existing:
        return order_to_response(existing)

    # ── 1. Валидация (схема уже проверила quantity >= 1, items not empty) ─────
    if not payload.items:
        raise order_http_error(
            status.HTTP_400_BAD_REQUEST,
            "INVALID_REQUEST",
            "Список items не может быть пустым",
        )

    # ── 2. Получаем данные SKU из B2B ─────────────────────────────────────────
    sku_ids = [item.sku_id for item in payload.items]
    try:
        sku_data_list = await get_products_by_sku_ids(sku_ids)
    except B2BUnavailableError:
        raise order_http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "B2B_UNAVAILABLE",
            "Сервис товаров временно недоступен",
        )

    sku_index = _build_sku_index(sku_data_list)

    # ── 3. Проверка статусов/остатков (до резервирования) ────────────────────
    failed_items = _validate_skus_availability(payload.items, sku_index)
    if failed_items:
        raise order_http_error(
            status.HTTP_409_CONFLICT,
            "RESERVE_FAILED",
            "Не удалось зарезервировать товары",
            failed_items=failed_items,
        )

    # ── 4. POST /reserve → B2B (all-or-nothing) ───────────────────────────────
    order_id = uuid.uuid4()
    reserve_items = [
        {"sku_id": str(item.sku_id), "quantity": item.quantity}
        for item in payload.items
    ]
    try:
        await reserve(
            idempotency_key=payload.idempotency_key,
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

    # ── 5. Создаём Order + OrderItem в транзакции (фиксация цен) ─────────────
    try:
        order = await _persist_order(
            db=db,
            order_id=order_id,
            user_id=user_id,
            payload=payload,
            sku_index=sku_index,
        )
    except IntegrityError:
        # Race: параллельный запрос с тем же idempotency_key уже вставил заказ
        await db.rollback()
        existing = await _get_order_by_idempotency_key(db, payload.idempotency_key)
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
    payload: CheckoutRequest,
    sku_index: Dict[str, Dict[str, Any]],
) -> Order:
    """Атомарная запись заказа с зафиксированными ценами."""
    total_amount = 0
    order_items: List[OrderItem] = []

    for item in payload.items:
        sku = sku_index[str(item.sku_id)]
        product = sku.get("product") or {}
        unit_price = sku["price"]
        line_total = unit_price * item.quantity
        total_amount += line_total

        order_items.append(OrderItem(
            id=uuid.uuid4(),
            order_id=order_id,
            sku_id=item.sku_id,
            product_id=uuid.UUID(sku["product_id"]),
            product_title=product.get("title", ""),
            sku_name=sku["name"],
            quantity=item.quantity,
            unit_price=unit_price,
            line_total=line_total,
        ))

    order = Order(
        id=order_id,
        user_id=user_id,
        status=OrderStatus.PAID,
        total_amount=total_amount,
        delivery_address=payload.delivery_address,
        idempotency_key=payload.idempotency_key,
    )
    order.items = order_items

    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order
