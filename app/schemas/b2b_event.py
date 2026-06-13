import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class IncomingB2BEventType(str, Enum):
    PRODUCT_CREATED = "PRODUCT_CREATED"
    PRODUCT_EDITED = "PRODUCT_EDITED"
    PRODUCT_DELETED = "PRODUCT_DELETED"


class EventProductCreated(BaseModel):
    model_config = {"extra": "forbid"}
    product_id: uuid.UUID
    seller_id: uuid.UUID
    category_id: uuid.UUID | None = None
    queue_priority: int | None = Field(default=3, ge=1, le=4)
    json_after: dict


class EventProductEdited(BaseModel):
    model_config = {"extra": "forbid"}
    product_id: uuid.UUID
    seller_id: uuid.UUID
    category_id: uuid.UUID | None = None
    queue_priority: int | None = Field(default=3, ge=1, le=4)
    json_before: dict
    json_after: dict


class EventProductDeleted(BaseModel):
    model_config = {"extra": "forbid"}
    product_id: uuid.UUID


class IncomingB2BEvent(BaseModel):
    event_type: IncomingB2BEventType
    idempotency_key: uuid.UUID
    occurred_at: datetime
    payload: EventProductCreated | EventProductEdited | EventProductDeleted
