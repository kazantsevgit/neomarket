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
from app.models.reservation import ReservationIdempotency, ReserveOperation
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


def _fulfill_idem_key(order_id: uuid.UUID) -> uuid.UUID:
    """Детерминированный idempotency_key для fulfill по order_id."""
    h = hashlib.sha256(f"fulfill:{order_id}".encode()).digest()[:16]
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
    asyncio.create_task(_send_out_of_stock(sku_id))


async def reserve_inventory(
    db: AsyncSession,
    payload: ReserveRequest,
) -> ReserveResponse:
    """
    All-or-nothing резервирование.
    1. Idempotency check.
    2. Схлопываем дубли sku_id.
    3. SELECT FOR UPDATE (отсортированы по sku_id для предотвращения дедлоков).
    4. Проверяем остатки — fail fast.
    5. Резервируем атомарно.
    6. Сохраняем ReserveOperation для последующего unreserve.
    7. Emit SKU_OUT_OF_STOCK.
    8. Сохраняем idempotency-запись.
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
    # Сортируем для предотвращения дедлоков при конкурентных транзакциях
    items = [
        InventoryItem(sku_id=k, quantity=v)
        for k, v in sorted(aggregated.items())
    ]

    # ── 3. SELECT FOR UPDATE ─────────────────────────────────────────────────
    result = await db.execute(
        select(SKU)
        .where(SKU.id.in_([item.sku_id for item in items]))
        .order_by(SKU.id)
        .with_for_update()
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
                "details": {"sku_ids": insufficient},
            },
        )

    # ── 5. Резервируем ───────────────────────────────────────────────────────
    out_of_stock_ids: List[uuid.UUID] = []
    for item in items:
        sku = skus[item.sku_id]
        sku.reserved_quantity += item.quantity
        if sku.active_quantity == 0:
            out_of_stock_ids.append(sku.id)

    # ── 6. Сохраняем ReserveOperation ────────────────────────────────────────
    for item in items:
        db.add(ReserveOperation(
            order_id=payload.order_id,
            sku_id=item.sku_id,
            quantity=item.quantity,
        ))

    # ── 7. Emit SKU_OUT_OF_STOCK ─────────────────────────────────────────────
    for sku_id in out_of_stock_ids:
        emit_sku_out_of_stock(sku_id)

    # ── 8. Idempotency record ────────────────────────────────────────────────
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
    Верифицирует по ReserveOperation: снимаем только то,
    что было реально зарезервировано под этот order_id.
    Идемпотентна: повторный вызов с тем же order_id → no-op.
    """
    idem_key = _unreserve_idem_key(order_id)

    # ── Idempotency ──────────────────────────────────────────────────────────
    existing: Optional[ReservationIdempotency] = await db.get(
        ReservationIdempotency, idem_key
    )
    if existing is not None:
        logger.info("duplicate unreserve order_id=%s, skipping", order_id)
        return InventoryOrderResponse(
            order_id=order_id, status="UNRESERVED", processed_at=_utcnow()
        )

    # ── Верификация: проверяем что резерв для order_id существует ────────────
    ops_result = await db.execute(
        select(ReserveOperation).where(ReserveOperation.order_id == order_id)
    )
    ops: dict[uuid.UUID, int] = {
        op.sku_id: op.quantity
        for op in ops_result.scalars().all()
    }

    if not ops:
        # Нет резерва по order_id — no-op, возвращаем 200 (идемпотентность)
        logger.info("no reservation found for order_id=%s, returning 200 no-op", order_id)
        return InventoryOrderResponse(
            order_id=order_id, status="UNRESERVED", processed_at=_utcnow()
        )

    # ── SELECT FOR UPDATE по реально зарезервированным SKU ──────────────────
    sku_ids = list(ops.keys())
    result = await db.execute(
        select(SKU).where(SKU.id.in_(sku_ids)).order_by(SKU.id).with_for_update()
    )
    skus: dict[uuid.UUID, SKU] = {sku.id: sku for sku in result.scalars().all()}

    # ── Снимаем только зарезервированное количество ──────────────────────────
    for sku_id, reserved_qty in ops.items():
        sku = skus.get(sku_id)
        if sku is None:
            continue
        sku.reserved_quantity = max(0, sku.reserved_quantity - reserved_qty)

    # ── Idempotency record ───────────────────────────────────────────────────
    db.add(ReservationIdempotency(
        idempotency_key=idem_key,
        order_id=order_id,
        response_payload={"status": "UNRESERVED"},
    ))
    await db.commit()
    return InventoryOrderResponse(
        order_id=order_id, status="UNRESERVED", processed_at=_utcnow()
    )


async def fulfill_inventory(
    db: AsyncSession,
    order_id: uuid.UUID,
    items: List[InventoryItem],
) -> InventoryOrderResponse:
    """
    Списание резерва при доставке.
    Уменьшает reserved_quantity и stock_quantity.
    Идемпотентна по order_id аналогично unreserve.
    """
    idem_key = _fulfill_idem_key(order_id)

    existing: Optional[ReservationIdempotency] = await db.get(
        ReservationIdempotency, idem_key
    )
    if existing is not None:
        logger.info("duplicate fulfill order_id=%s, skipping", order_id)
        return InventoryOrderResponse(
            order_id=order_id, status="FULFILLED", processed_at=_utcnow()
        )

    # Верификация: резерв должен существовать
    ops_result = await db.execute(
        select(ReserveOperation).where(ReserveOperation.order_id == order_id)
    )
    ops: dict[uuid.UUID, int] = {
        op.sku_id: op.quantity
        for op in ops_result.scalars().all()
    }

    if not ops:
        # Нет резерва по order_id — no-op, возвращаем 200 (идемпотентность)
        logger.info("no reservation found for order_id=%s, returning 200 no-op", order_id)
        return InventoryOrderResponse(
            order_id=order_id, status="UNRESERVED", processed_at=_utcnow()
        )

    sku_ids = list(ops.keys())
    result = await db.execute(
        select(SKU).where(SKU.id.in_(sku_ids)).order_by(SKU.id).with_for_update()
    )
    skus: dict[uuid.UUID, SKU] = {sku.id: sku for sku in result.scalars().all()}

    for sku_id, reserved_qty in ops.items():
        sku = skus.get(sku_id)
        if sku is None:
            continue
        sku.reserved_quantity = max(0, sku.reserved_quantity - reserved_qty)
        sku.stock_quantity = max(0, sku.stock_quantity - reserved_qty)

    db.add(ReservationIdempotency(
        idempotency_key=idem_key,
        order_id=order_id,
        response_payload={"status": "FULFILLED"},
    ))
    await db.commit()
    return InventoryOrderResponse(
        order_id=order_id, status="FULFILLED", processed_at=_utcnow()
    )
