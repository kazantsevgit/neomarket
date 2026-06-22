import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


class ProductEventType(str, Enum):
    PRODUCT_BLOCKED = "PRODUCT_BLOCKED"
    PRODUCT_DELETED = "PRODUCT_DELETED"
    SKU_OUT_OF_STOCK = "SKU_OUT_OF_STOCK"


# ─── Payload variants ─────────────────────────────────────────────────────────

class PayloadProductRef(BaseModel):
    """PRODUCT_BLOCKED / PRODUCT_DELETED: только product_id."""
    product_id: uuid.UUID


class PayloadSkuOutOfStock(BaseModel):
    """SKU_OUT_OF_STOCK: sku_id + product_id + available_quantity."""
    sku_id: uuid.UUID
    product_id: uuid.UUID
    available_quantity: int


# ─── Request / Response ───────────────────────────────────────────────────────

class ProductEventRequest(BaseModel):
    event_type: ProductEventType
    idempotency_key: uuid.UUID
    occurred_at: datetime
    payload: dict[str, Any]          # гибкий payload; конкретные поля читаем в сервисе


class ProductEventResponse(BaseModel):
    accepted: bool