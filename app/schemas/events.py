import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ProductEventType(str, Enum):
    PRODUCT_BLOCKED = "PRODUCT_BLOCKED"
    PRODUCT_DELETED = "PRODUCT_DELETED"
    SKU_OUT_OF_STOCK = "SKU_OUT_OF_STOCK"


class ProductEventRequest(BaseModel):
    idempotency_key: uuid.UUID
    event: ProductEventType
    product_id: uuid.UUID
    sku_ids: list[uuid.UUID] = Field(..., min_length=1)
    reason: Optional[str] = None
    date: datetime


class ProductEventResponse(BaseModel):
    accepted: bool
