import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class ProductImageCreate(BaseModel):
    url: str
    ordering: int = 0


class ProductImageResponse(BaseModel):
    id: uuid.UUID
    url: str
    ordering: int


class Characteristic(BaseModel):
    name: str
    value: str


class CharacteristicResponse(Characteristic):
    id: uuid.UUID


class ProductCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    category_id: uuid.UUID
    attributes: Optional[Dict[str, Any]] = Field(default_factory=dict)
    images: List[ProductImageCreate] = Field(..., min_length=1)

    @field_validator("images")
    @classmethod
    def images_not_empty(cls, v: List[ProductImageCreate]) -> List[ProductImageCreate]:
        if not v:
            raise ValueError("images must contain at least one image")
        return v


class ProductResponse(BaseModel):
    id: uuid.UUID
    seller_id: uuid.UUID
    title: str
    slug: str
    description: Optional[str]
    category_id: uuid.UUID
    status: str
    deleted: bool
    blocking_reason_id: Optional[uuid.UUID] = None
    moderator_comment: Optional[str] = None
    images: List[ProductImageResponse]
    characteristics: List[CharacteristicResponse]
    skus: List[SKUResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
