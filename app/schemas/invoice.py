import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class InvoiceItemCreate(BaseModel):
    sku_id: uuid.UUID
    quantity: int = Field(..., gt=0)


class InvoiceCreate(BaseModel):
    items: list[InvoiceItemCreate] = Field(...)


class InvoiceItemResponse(BaseModel):
    id: uuid.UUID
    sku_id: uuid.UUID
    sku_name: str
    quantity: int
    accepted_quantity: Optional[int] = None

    class Config:
        from_attributes = True


class InvoiceResponse(BaseModel):
    id: uuid.UUID
    status: str
    created_at: datetime
    items: list[InvoiceItemResponse]

    class Config:
        from_attributes = True
