import uuid
from typing import Optional

from pydantic import BaseModel


class ImageRef(BaseModel):
    id: uuid.UUID
    url: str
    alt: Optional[str] = None
    ordering: int = 0
    is_main: bool = False

    model_config = {"from_attributes": True}


class CategoryRef(BaseModel):
    id: uuid.UUID
    name: str
    level: int = 0
    path: list[str] = []

    model_config = {"from_attributes": True}


class CatalogProductCard(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    category: CategoryRef
    min_price: int
    old_price: Optional[int] = None
    has_stock: bool
    rating: Optional[float] = None
    reviews_count: int = 0
    images: list[ImageRef] = []
    seller: dict = {}

    model_config = {"from_attributes": True}


class PaginatedCatalogProducts(BaseModel):
    items: list[CatalogProductCard]
    total_count: int
    limit: int
    offset: int

    model_config = {"from_attributes": True}
