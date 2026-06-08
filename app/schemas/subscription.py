import enum
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


class SubscribeEventType(str, enum.Enum):
    IN_STOCK = "IN_STOCK"
    PRICE_DOWN = "PRICE_DOWN"


class SubscribeRequest(BaseModel):
    notify_on: list[SubscribeEventType]

    @field_validator("notify_on")
    @classmethod
    def check_not_empty(cls, v: list[SubscribeEventType]) -> list[SubscribeEventType]:
        if not v:
            raise ValueError("notify_on must not be empty")
        return v


class SubscriptionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    product_id: uuid.UUID
    notify_on: list[str]
    created_at: datetime

    model_config = {"from_attributes": True}
