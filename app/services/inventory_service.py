import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import SKU
from app.models.reservation import ReservationIdempotency
from app.schemas.inventory import (
    InventoryItem,
    InventoryOrderResponse,
    ReserveRequest,
    ReserveResponse,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def emit_sku_out_of_stock(sku_id: uuid.UUID) -> None:
    """
    Заглушка — в production здесь HTTP/gRPC-вызов в B2C или публикация в очередь.
    Вынесена отдельной функцией, чтобы легко мокать в тестах.
    """
    pass  # pragma: no cover


async def reserve_inventory(
    db: AsyncSession,
    payload: ReserveRequest,
) -> ReserveResponse:
    """
    All-or-nothing резервирование.
    1. Проверяем idempotency_key — если уже обрабатывали, возвращаем кешированный ответ.
    2. SELECT FOR UPDATE по всем sku_id.
    3. Проверяем active_quantity для каждого SKU — если хоть один не хватает → 409, rollback.
    4. Атомарно увеличиваем reserved_quantity по всем SKU.
    5. Если active_quantity стал 0 → emit SKU_OUT_OF_STOCK.
    6. Сохраняем idempotency-запись.
    """
    # ── 1. Idempotency check ─────────────────────────────────────────────────
    existing: ReservationIdempotency | None = await db.get(
        ReservationIdempotency, payload.idempotency_key
    )
    if existing is not None:
        return ReserveResponse(**existing.response_payload)

    # ── 2. SELECT FOR UPDATE ─────────────────────────────────────────────────
    sku_ids = [item.sku_id for item in payload.items]
    result = await db.execute(
        select(SKU)
        .where(SKU.id.in_(sku_ids))
        .with_for_update()
    )
    skus: dict[uuid.UUID, SKU] = {sku.id: sku for sku in result.scalars().all()}

    # ── 3. Проверка остатков (all-or-nothing: fail fast) ─────────────────────
    insufficient: List[str] = []
    for item in payload.items:
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
                "message": "Insufficient stock",
                "sku_ids": insufficient,
            },
        )

    # ── 4. Резервируем атомарно ───────────────────────────────────────────────
    out_of_stock_ids: List[uuid.UUID] = []
    for item in payload.items:
        sku = skus[item.sku_id]
        sku.reserved_quantity += item.quantity
        if sku.active_quantity == 0:
            out_of_stock_ids.append(sku.id)

    # ── 5. События SKU_OUT_OF_STOCK ───────────────────────────────────────────
    for sku_id in out_of_stock_ids:
        emit_sku_out_of_stock(sku_id)

    # ── 6. Сохраняем idempotency-запись ──────────────────────────────────────
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
    Идемпотентна: повторный вызов с теми же данными не ломает инвариант
    (reserved_quantity не уходит ниже 0).
    """
    sku_ids = [item.sku_id for item in items]
    result = await db.execute(
        select(SKU)
        .where(SKU.id.in_(sku_ids))
        .with_for_update()
    )
    skus: dict[uuid.UUID, SKU] = {sku.id: sku for sku in result.scalars().all()}

    for item in items:
        sku = skus.get(item.sku_id)
        if sku is None:
            continue  # SKU уже удалён — игнорируем
        # Защита от отрицательного reserved_quantity
        sku.reserved_quantity = max(0, sku.reserved_quantity - item.quantity)

    await db.commit()
    return InventoryOrderResponse(
        order_id=order_id,
        status="UNRESERVED",
        processed_at=_utcnow(),
    )
