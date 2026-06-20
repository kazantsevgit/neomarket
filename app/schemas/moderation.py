import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class FieldName(str, Enum):
    TITLE = "title"
    DESCRIPTION = "description"
    PRODUCT_IMAGES = "product_images"
    CATEGORY = "category"
    SKU_NAME = "sku_name"
    SKU_IMAGE = "sku_image"
    SKU_PRICE = "sku_price"


class DeclineFieldReport(BaseModel):
    field_name: FieldName
    sku_id: Optional[uuid.UUID] = None
    comment: str = Field(..., max_length=500)


class DeclineProductRequest(BaseModel):
    blocking_reason_id: uuid.UUID
    moderator_comment: Optional[str] = Field(None, max_length=2000)
    field_reports: Optional[list[DeclineFieldReport]] = None


class DeclineProductResponse(BaseModel):
    product_id: uuid.UUID
    status: str


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


class TicketResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    seller_id: uuid.UUID
    category_id: uuid.UUID | None = None
    kind: str
    status: str
    queue_priority: int = 3
    assigned_moderator_id: uuid.UUID | None = None
    claimed_at: datetime | None = None
    claim_expires_at: datetime | None = None
    decision_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class BlockFieldReport(BaseModel):
    field_path: str
    message: str


class BlockDecisionRequest(BaseModel):
    blocking_reason_ids: list[uuid.UUID]
    comment: str | None = Field(None, max_length=2000)
    field_reports: list[BlockFieldReport] | None = None


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


class GetNextRequest(BaseModel):
    queue_priority: int | None = Field(
        None, ge=1, le=4,
        description="Номер очереди (1-4). Если null — автоприоритизация 1→4",
    )


class BlockingHistoryBlockingReason(BaseModel):
    id: uuid.UUID
    title: str


class BlockingHistory(BaseModel):
    blocking_reason: BlockingHistoryBlockingReason
    moderator_comment: str | None = None
    field_reports: list[dict] | None = None
    date_blocked: datetime | None = None


class GetNextResponse(BaseModel):
    id: uuid.UUID
    product_moderation_id: uuid.UUID
    product_id: uuid.UUID
    seller_id: uuid.UUID
    kind: str
    status: str
    queue_priority: int
    json_before: dict | None = None
    json_after: dict | None = None
    blocking_history: BlockingHistory | None = None
    created_at: datetime
    date_updated: datetime
