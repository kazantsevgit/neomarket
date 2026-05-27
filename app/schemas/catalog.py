import uuid
from typing import Any

from pydantic import BaseModel, Field


class ProductShortItem(BaseModel):
    id: uuid.UUID
    title: str
    image: str | None = None
    price: int
    in_stock: bool = True
    is_in_cart: bool = False


class ProductShortListResponse(BaseModel):
    items: list[ProductShortItem]
    total_count: int
    limit: int
    offset: int


class FacetValueCount(BaseModel):
    value: str
    count: int


class FacetGroup(BaseModel):
    name: str
    values: list[FacetValueCount]


class FacetsResponse(BaseModel):
    category_id: uuid.UUID | None = None
    facets: list[FacetGroup]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
