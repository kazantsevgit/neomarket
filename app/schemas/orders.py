"""
Pydantic-схемы для B2C Orders (checkout, list, detail).

Соответствие OpenAPI: flows/b2c-orders-flows.md#b2c-9-checkout
"""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Request ───────────────────────────────────────────────────────────────────

class CheckoutItemSnapshot(BaseModel):
    """Опциональный снапшот корзины для защиты от гонок (OpenAPI items_snapshot)."""
    sku_id: uuid.UUID
    quantity: int = Field(..., ge=1)
    unit_price: int = Field(..., ge=0)


class CheckoutRequest(BaseModel):
    address_id: uuid.UUID
    payment_method_id: uuid.UUID
    comment: Optional[str] = Field(None, max_length=1000)
    items_snapshot: Optional[List[CheckoutItemSnapshot]] = None


class CheckoutItem(BaseModel):
    """Внутренняя позиция checkout — собирается из корзины покупателя."""
    sku_id: uuid.UUID
    quantity: int = Field(..., ge=1)


# ── Response ──────────────────────────────────────────────────────────────────

class OrderItemResponse(BaseModel):
    id: uuid.UUID
    sku_id: uuid.UUID
    product_id: uuid.UUID
    name: str
    product_title: str
    sku_name: str
    quantity: int
    unit_price: int
    line_total: int


class OrderResponse(BaseModel):
    id: uuid.UUID
    buyer_id: uuid.UUID
    status: str
    items: List[OrderItemResponse]
    subtotal: int
    total: int
    delivery_address: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# ── Error bodies ──────────────────────────────────────────────────────────────

class FailedItem(BaseModel):
    sku_id: uuid.UUID
    requested: Optional[int] = None
    available: Optional[int] = None
    reason: str


class ReserveFailedError(BaseModel):
    code: str = "RESERVE_FAILED"
    message: str = "Не удалось зарезервировать товары"
    failed_items: List[FailedItem]
