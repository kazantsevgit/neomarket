import logging
import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_idempotency import EventIdempotencyKey
from app.schemas.events import ProductEventRequest, ProductEventType

logger = logging.getLogger(__name__)

_EVENT_TO_REASON: dict[ProductEventType, str] = {
    ProductEventType.PRODUCT_BLOCKED: "PRODUCT_BLOCKED",
    ProductEventType.PRODUCT_DELETED: "PRODUCT_DELETED",
    ProductEventType.SKU_OUT_OF_STOCK: "OUT_OF_STOCK",
}


def _extract_sku_ids(payload: ProductEventRequest) -> list[str]:
    """
    Извлекает список sku_ids из payload события.

    PRODUCT_BLOCKED / PRODUCT_DELETED — payload: {product_id}
      Корзину помечаем по product_id (через JOIN или подзапрос).
      Для batch UPDATE передаём product_id как фильтр.
    SKU_OUT_OF_STOCK — payload: {sku_id, product_id, available_quantity}
      Обновляем только конкретный sku_id.
    """
    p = payload.payload
    if payload.event_type == ProductEventType.SKU_OUT_OF_STOCK:
        sku_id = p.get("sku_id")
        return [str(sku_id)] if sku_id else []
    else:
        # PRODUCT_BLOCKED / PRODUCT_DELETED: обновляем все sku этого product
        product_id = p.get("product_id")
        return [str(product_id)] if product_id else []


async def process_product_event(
    db: AsyncSession,
    payload: ProductEventRequest,
) -> None:
    """
    Обрабатывает событие от B2B.

    1. Idempotency check — если уже обработано → 409 Conflict.
    2. Batch UPDATE cart_items.
    3. Сохраняем idempotency-запись.
    Заказы (orders/order_items) не трогаем.
    """
    # ── 1. Idempotency check ────────────────────────────────────────────
    existing: Optional[EventIdempotencyKey] = await db.get(
        EventIdempotencyKey, payload.idempotency_key
    )
    if existing is not None:
        logger.info(
            "duplicate product event idempotency_key=%s, returning 409",
            payload.idempotency_key,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONFLICT", "message": "Event already processed"},
        )

    # ── 2. Batch UPDATE cart_items ──────────────────────────────────────
    reason = _EVENT_TO_REASON[payload.event_type]
    product_id = payload.payload.get("product_id")

    if payload.event_type == ProductEventType.SKU_OUT_OF_STOCK:
        sku_id = payload.payload.get("sku_id")
        stmt = text(
            "UPDATE cart_items "
            "SET unavailable_reason = :reason, updated_at = NOW() "
            "WHERE sku_id = :sku_id::uuid "
            "AND (unavailable_reason IS NULL OR unavailable_reason != :reason)"
        )
        await db.execute(stmt, {"reason": reason, "sku_id": str(sku_id)})
    else:
        # PRODUCT_BLOCKED / PRODUCT_DELETED — по product_id через SKU
        stmt = text(
            "UPDATE cart_items "
            "SET unavailable_reason = :reason, updated_at = NOW() "
            "WHERE sku_id IN ("
            "  SELECT id FROM skus WHERE product_id = :product_id::uuid"
            ") "
            "AND (unavailable_reason IS NULL OR unavailable_reason != :reason)"
        )
        await db.execute(stmt, {"reason": reason, "product_id": str(product_id)})

    # ── 3. Idempotency record ───────────────────────────────────────────
    db.add(
        EventIdempotencyKey(
            idempotency_key=payload.idempotency_key,
            event=payload.event_type.value,
            product_id=uuid.UUID(str(product_id)) if product_id else None,
        )
    )
    await db.commit()
    logger.info(
        "processed product event event=%s product_id=%s",
        payload.event_type.value,
        product_id,
    )