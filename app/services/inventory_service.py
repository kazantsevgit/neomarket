import asyncio
import hashlib
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.product import SKU
from app.models.reservation import ReservationIdempotency
from app.schemas.inventory import (
    InventoryItem,
    InventoryOrderResponse,
    ReserveRequest,
    ReserveResponse,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _unreserve_idem_key(order_id: uuid.UUID) -> uuid.UUID:
    """Детерминированный idempotency_key для unreserve по order_id."""
    h = hashlib.sha256(f"unreserve:{order_id}".encode()).digest()[:16]
    return uuid.UUID(bytes=h)


async def _send_out_of_stock(sku_id: uuid.UUID) -> None:
    """Реальная отправка SKU_OUT_OF_STOCK в B2C (fire-and-forget)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{settings.B2C_URL}/api/v1/b2b/events",
                json={"event_type": "SKU_OUT_OF_STOCK", "sku_id": str(sku_id)},
                headers={"X-Service-Key": settings.SERVICE_KEY},
            )
        logger.info("SKU_OUT_OF_STOCK sent sku_id=%s", sku_id)
    except Exception as exc:
        logger.error("failed to send SKU_OUT_OF_STOCK sku_id=%s: %s", sku_id, exc)


def emit_sku_out_of_stock(sku_id: uuid.UUID) -> None:
    """Fire-and-forget отправка события."""
    asyncio.create_task(_send_out_of_stock(sku_id))


async def reserve_inventory(
    db: AsyncSession,
    payload: ReserveRequest,
) -> ReserveResponse:
    """
    All-or-nothing резервирование.
    1. Idempotency check.
    2. Схлопываем дубли sku_id (суммируем quantity).
    3. SELECT FOR UPDATE.
    4. Проверяем остатки — fail fast.
    5. Резервируем атомарно.
    6. Emit SKU_OUT_OF_STOCK если active_quantity стал 0.
    7. Сохраняем idempotency-запись.
    """
    # ── 1. Idempotency ───────────────────────────────────────────────────────
    existing: Optional[ReservationIdempotency] = await db.get(
        ReservationIdempotency, payload.idempotency_key
    )
    if existing is not None:
        return ReserveResponse(**existing.response_payload)

    # ── 2. Схлопываем дубли sku_id ───────────────────────────────────────────
    aggregated: dict[uuid.UUID, int] = defaultdict(int)
    for item in payload.items:
        aggregated[item.sku_id] += item.quantity
    items = [InventoryItem(sku_id=k, quantity=v) for k, v in aggregated.items()]

    # ── 3. SELECT FOR UPDATE ─────────────────────────────────────────────────
    result = await db.execute(
        select(SKU).where(SKU.id.in_(list(aggregated.keys()))).with_for_update()
    )
    skus: dict[uuid.UUID, SKU] = {sku.id: sku for sku in result.scalars().all()}

    # ── 4. Проверка остатков ─────────────────────────────────────────────────
    insufficient: List[str] = []
    for item in items:
        sku = skus.get(item.sku_id)
        if sku is None:
            insufficient.append(str(item.sku_id))
            continue
        if sku.active_quantity < item.quantity:
            insufficient.append(str(item.sku_id))

    if insufficient:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "INSUFFICIENT_STOCK",
                "message": "Insufficient stock for one or more SKUs",
                "sku_ids": insufficient,
            },
        )

    # ── 5. Резервируем ───────────────────────────────────────────────────────
    out_of_stock_ids: List[uuid.UUID] = []
    for item in items:
        sku = skus[item.sku_id]
        sku.reserved_quantity += item.quantity
        if sku.active_quantity == 0:
            out_of_stock_ids.append(sku.id)

    # ── 6. SKU_OUT_OF_STOCK ──────────────────────────────────────────────────
    for sku_id in out_of_stock_ids:
        emit_sku_out_of_stock(sku_id)

    # ── 7. Idempotency record ────────────────────────────────────────────────
    reserved_at = _utcnow()
    response = ReserveResponse(
        order_id=payload.order_id,
        status="RESERVED",
        reserved_at=reserved_at,
    )
    db.add(ReservationIdempotency(
        idempotency_key=payload.idempotency_key,
        order_id=payload.order_id,
        response_payload=response.model_dump(mode="json"),
    ))
    await db.commit()
    return response


async def unreserve_inventory(
    db: AsyncSession,
    order_id: uuid.UUID,
    items: List[InventoryItem],
) -> InventoryOrderResponse:
    """
    Компенсирующая операция — снимает резерв.
    Идемпотентна по order_id: повторный вызов → no-op.
    """
    idem_key = _unreserve_idem_key(order_id)

    existing: Optional[ReservationIdempotency] = await db.get(
        ReservationIdempotency, idem_key
    )
    if existing is not None:
        logger.info("duplicate unreserve order_id=%s, skipping", order_id)
        return InventoryOrderResponse(
            order_id=order_id, status="UNRESERVED", processed_at=_utcnow()
        )

    sku_ids = [item.sku_id for item in items]
    result = await db.execute(
        select(SKU).where(SKU.id.in_(sku_ids)).with_for_update()
    )
    skus: dict[uuid.UUID, SKU] = {sku.id: sku for sku in result.scalars().all()}

    for item in items:
        sku = skus.get(item.sku_id)
        if sku is None:
            continue
        sku.reserved_quantity = max(0, sku.reserved_quantity - item.quantity)

    db.add(ReservationIdempotency(
        idempotency_key=idem_key,
        order_id=order_id,
        response_payload={"status": "UNRESERVED"},
    ))
    await db.commit()
    return InventoryOrderResponse(
        order_id=order_id, status="UNRESERVED", processed_at=_utcnow()
    )
