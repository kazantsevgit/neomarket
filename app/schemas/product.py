import uuid
from decimal import Decimal
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field, field_validator


# ── SKU ──────────────────────────────────────────────────────────────────────

class SKUCreate(BaseModel):
    product_id: uuid.UUID
    price: Decimal = Field(..., gt=0, description="Цена SKU, должна быть > 0")
    images: List[str] = Field(..., min_length=1, description="Минимум одно фото")
    attributes: Optional[Dict[str, Any]] = Field(default_factory=dict)

    @field_validator("images")
    @classmethod
    def images_not_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("images must contain at least one URL")
        return v


class SKUResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    price: Decimal
    images: List[str]
    attributes: Dict[str, Any]

    model_config = {"from_attributes": True}


# ── Product ───────────────────────────────────────────────────────────────────

class ProductCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    category_id: uuid.UUID
    attributes: Optional[Dict[str, Any]] = Field(default_factory=dict)
    images: List[str] = Field(..., min_length=1)

    @field_validator("images")
    @classmethod
    def images_not_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("images must contain at least one URL")
        return v


class ProductResponse(BaseModel):
    id: uuid.UUID
    seller_id: uuid.UUID
    title: str
    description: Optional[str]
    category_id: uuid.UUID
    attributes: Dict[str, Any]
    images: List[str]
    status: str
    skus: List[SKUResponse] = []

    model_config = {"from_attributes": True}