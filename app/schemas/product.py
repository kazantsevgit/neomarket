import uuid
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator


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


class SKUResponse(BaseModel):
    pass  # SKU — ответственность US-B2B-02, здесь пустой список


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