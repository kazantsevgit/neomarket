import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ── Вспомогательные схемы ─────────────────────────────────────────────────────

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


# ── SKU ──────────────────────────────────────────────────────────────────────

class SKUImageCreate(BaseModel):
    url: str
    ordering: int = 0


class SKUImageResponse(BaseModel):
    id: uuid.UUID
    url: str
    ordering: int

    model_config = {"from_attributes": True}


class SKUCreate(BaseModel):
    product_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=255)   # блокер 2: обязательное поле
    price: int  = Field(..., ge=0, description="Цена в копейках")
    discount:   int  = Field(default=0, ge=0, description="Скидка в копейках")
    cost_price: Optional[int]  = Field(default=None, description="Себестоимость в копейках")
    article:    Optional[str]  = None
    images:     List[SKUImageCreate]     = Field(default_factory=list)
    characteristics: List[Characteristic] = Field(default_factory=list)


class SKUResponse(BaseModel):
    """Seller-view SKU — соответствует neomarket-b2b.yaml:1284-1318."""
    id:               uuid.UUID
    product_id:       uuid.UUID
    name:             str
    price:            int
    discount:         int
    cost_price:       Optional[int]
    stock_quantity:   int
    active_quantity:  int
    reserved_quantity: int
    article:          Optional[str]
    images:           List[SKUImageResponse]
    characteristics:  List[CharacteristicResponse]
    created_at:       datetime
    updated_at:       datetime

    model_config = {"from_attributes": True}


# ── Product ───────────────────────────────────────────────────────────────────

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
