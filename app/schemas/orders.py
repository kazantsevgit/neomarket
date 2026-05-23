"""
Pydantic-схемы для B2C Orders (checkout, list, detail).

Соответствие OpenAPI: flows/b2c-orders-flows.md#b2c-9-checkout
"""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Request ───────────────────────────────────────────────────────────────────

class CheckoutItem(BaseModel):
    sku_id: uuid.UUID
    quantity: int = Field(..., ge=1)


class CheckoutRequest(BaseModel):
    idempotency_key: uuid.UUID
    items: List[CheckoutItem] = Field(..., min_length=1)
    delivery_address: Optional[str] = None


# ── Response ──────────────────────────────────────────────────────────────────

class OrderItemResponse(BaseModel):
    id: uuid.UUID
    sku_id: uuid.UUID
    product_id: uuid.UUID
    product_title: str
    sku_name: str
    quantity: int
    unit_price: int
    line_total: int

    model_config = {"from_attributes": True}


class OrderResponse(BaseModel):
    id: uuid.UUID
    status: str
    items: List[OrderItemResponse]
    total_amount: int
    delivery_address: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


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
