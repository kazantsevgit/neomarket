import enum
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


class SubscribeEventType(str, enum.Enum):
    BACK_IN_STOCK = "BACK_IN_STOCK"
    PRICE_DROP = "PRICE_DROP"


class SubscribeRequest(BaseModel):
    events: list[SubscribeEventType]

    @field_validator("events")
    @classmethod
    def check_not_empty(cls, v: list[SubscribeEventType]) -> list[SubscribeEventType]:
        if not v:
            raise ValueError("events must not be empty")
        return v


class SubscriptionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    product_id: uuid.UUID
    events: list[str]
    created_at: datetime

    model_config = {"from_attributes": True}
