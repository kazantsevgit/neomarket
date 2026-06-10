import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class B2BEventType(str, Enum):
    CREATED = "CREATED"
    EDITED = "EDITED"
    DELETED = "DELETED"


class B2BProductEventRequest(BaseModel):
    product_id: uuid.UUID = Field(..., description="ID товара в B2B")
    seller_id: uuid.UUID = Field(..., description="ID продавца")
    event: B2BEventType = Field(..., description="CREATED, EDITED, DELETED")
    date: datetime = Field(..., description="Время события в B2B (ISO 8601)")
