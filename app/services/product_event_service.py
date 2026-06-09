import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cart import CartItem
from app.models.event_idempotency import EventIdempotencyKey
from app.schemas.events import ProductEventRequest, ProductEventType

logger = logging.getLogger(__name__)

_EVENT_TO_REASON: dict[ProductEventType, str] = {
    ProductEventType.PRODUCT_BLOCKED: "PRODUCT_BLOCKED",
    ProductEventType.PRODUCT_DELETED: "PRODUCT_DELETED",
    ProductEventType.SKU_OUT_OF_STOCK: "OUT_OF_STOCK",
}


async def process_product_event(
    db: AsyncSession,
    payload: ProductEventRequest,
) -> None:
    """
    Обрабатывает событие от B2B (PRODUCT_BLOCKED / PRODUCT_DELETED / SKU_OUT_OF_STOCK).

    1. Проверяем idempotency_key — если уже обработано, выходим без эффекта.
    2. Batch-обновляем cart_items: устанавливаем unavailable_reason для всех sku_id из события.
    3. Сохраняем idempotency-запись.
    """
    # ── 1. Idempotency check ───────────────────────────────────────────
    existing: Optional[EventIdempotencyKey] = await db.get(
        EventIdempotencyKey, payload.idempotency_key
    )
    if existing is not None:
        logger.info(
            "duplicate product event idempotency_key=%s, skipping",
            payload.idempotency_key,
        )
        return

    # ── 2. Batch UPDATE cart_items ──────────────────────────────────────
    reason = _EVENT_TO_REASON[payload.event]
    sku_ids = [str(sku) for sku in payload.sku_ids]

    stmt = text(
        "UPDATE cart_items "
        "SET unavailable_reason = :reason, updated_at = NOW() "
        "WHERE sku_id = ANY(:sku_ids::uuid[]) "
        "AND (unavailable_reason IS NULL OR unavailable_reason != :reason)"
    )
    await db.execute(stmt, {"reason": reason, "sku_ids": sku_ids})

    # ── 3. Save idempotency record ──────────────────────────────────────
    db.add(
        EventIdempotencyKey(
            idempotency_key=payload.idempotency_key,
            event=payload.event.value,
            product_id=payload.product_id,
        )
    )

    await db.commit()
    logger.info(
        "processed product event event=%s product_id=%s sku_ids=%s",
        payload.event.value,
        payload.product_id,
        sku_ids,
    )
