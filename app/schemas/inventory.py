import uuid
from datetime import datetime
from typing import List

from pydantic import BaseModel, Field


class InventoryItem(BaseModel):
    sku_id: uuid.UUID
    quantity: int = Field(..., ge=1)


class ReserveRequest(BaseModel):
    idempotency_key: uuid.UUID
    order_id: uuid.UUID
    items: List[InventoryItem] = Field(..., min_length=1)


class ReserveResponse(BaseModel):
    order_id: uuid.UUID
    status: str = "RESERVED"
    reserved_at: datetime


class InventoryOrderRequest(BaseModel):
    order_id: uuid.UUID
    items: List[InventoryItem] = Field(..., min_length=1)


class InventoryOrderResponse(BaseModel):
    order_id: uuid.UUID
    status: str  # UNRESERVED | FULFILLED
    processed_at: datetime