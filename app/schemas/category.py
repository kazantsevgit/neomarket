"""
Схемы для US-CAT-05: навигация по категориям.

Эндпоинты:
  GET /api/v1/categories              → CategoryTreeResponse
  GET /api/v1/categories/{id}         → CategoryDetailResponse
  GET /api/v1/breadcrumbs             → BreadcrumbsResponse
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel


# ─── Дерево категорий (5a) ────────────────────────────────────────────────────

class CategoryTreeItem(BaseModel):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None = None
    children: list[CategoryTreeItem] = []

    model_config = {"from_attributes": True}


class CategoryTreeResponse(BaseModel):
    items: list[CategoryTreeItem]


# ─── Детали категории (5b) ────────────────────────────────────────────────────

class CategoryParentRef(BaseModel):
    id: uuid.UUID
    name: str
    slug: str | None = None

    model_config = {"from_attributes": True}


class CategoryDetailResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str | None = None
    description: str | None = None
    parent: CategoryParentRef | None = None
    product_count: int | None = None
    seo: dict[str, Any] | None = None
    meta_tags: dict[str, Any] | None = None
    image_url: str | None = None
    is_active: bool = True
    created_at: str | None = None
    updated_at: str | None = None

    model_config = {"from_attributes": True}


# ─── Навигационная цепочка (5d) ───────────────────────────────────────────────

class BreadcrumbItem(BaseModel):
    id: uuid.UUID
    slug: str | None = None
    name: str
    url: str
    level: int
    is_current: bool


class BreadcrumbMeta(BaseModel):
    resolved_via: str          # "category_id" | "product_id"
    category_id: uuid.UUID


class BreadcrumbsResponse(BaseModel):
    data: list[BreadcrumbItem]
    meta: BreadcrumbMeta