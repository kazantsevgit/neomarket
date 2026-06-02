import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ModerationEventType(str, Enum):
    MODERATED = "MODERATED"
    BLOCKED = "BLOCKED"


class FieldReport(BaseModel):
    field_name: str = Field(..., description='например "title", "description", "images[0]"')
    sku_id: Optional[uuid.UUID] = None
    comment: str


class DeclineRequest(BaseModel):
    blocking_reason_id: uuid.UUID
    moderator_comment: Optional[str] = Field(None, max_length=2000)
    field_reports: Optional[List[FieldReport]] = None


class DeclineResponse(BaseModel):
    product_id: uuid.UUID
    status: str


class ApproveRequest(BaseModel):
    moderator_comment: Optional[str] = Field(None, max_length=2000)


class ApproveResponse(BaseModel):
    product_id: uuid.UUID
    status: str


class ModerationEventRequest(BaseModel):
    idempotency_key: uuid.UUID
    product_id: uuid.UUID
    event_type: ModerationEventType
    moderator_id: Optional[uuid.UUID] = None
    moderator_comment: Optional[str] = None
    blocking_reason_id: Optional[uuid.UUID] = Field(
        None, description="Обязательно при BLOCKED"
    )
    hard_block: bool = Field(default=False, description="При true → HARD_BLOCKED")
    field_reports: Optional[List[FieldReport]] = None
    occurred_at: datetime
