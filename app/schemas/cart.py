"""
Pydantic-схемы для корзины (US-CART-03).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Requests ────────────────────────────────────────────────────────────────


class CartItemAddRequest(BaseModel):
    sku_id: uuid.UUID
    quantity: int = Field(..., ge=1)


class CartItemUpdateRequest(BaseModel):
    quantity: int = Field(..., ge=1)


# ── Responses ───────────────────────────────────────────────────────────────


class CartItem(BaseModel):
    sku_id: uuid.UUID
    product_id: uuid.UUID
    name: str
    sku_code: Optional[str] = None
    quantity: int

    unit_price: int
    unit_price_at_add: Optional[int] = None
    line_total: int

    available_quantity: int = Field(..., ge=0)
    is_available: bool

    # computed on every GET /cart enrichment (не хранится в БД)
    unavailable_reason: Optional[str] = None

    # MVP: картинка опциональна (может прийти из B2B, если endpoint поддерживает)
    image: Optional[dict] = None


class CartResponse(BaseModel):
    id: Optional[uuid.UUID] = None
    items: list[CartItem]
    items_count: int
    subtotal: int
    is_valid: bool
    updated_at: datetime


# ── Validate response (минимальная реализация) ─────────────────────────────


class CartValidationIssue(BaseModel):
    sku_id: uuid.UUID
    type: str
    message: str
    old_value: Optional[int | str] = None
    new_value: Optional[int | str] = None


class CartValidationResponse(BaseModel):
    is_valid: bool
    cart: CartResponse
    issues: list[CartValidationIssue] = []

