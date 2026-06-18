"""
US-CAT-05: навигация по категориям.

Endpoints:
  GET /api/v1/categories              — дерево категорий (5a)
  GET /api/v1/categories/{id}         — детали категории (5b)
  GET /api/v1/breadcrumbs             — навигационная цепочка (5d)

Параметры breadcrumbs: ровно один из category_id / product_id.
  • Оба переданы         → 400 ambiguous_param
  • Ни одного            → 400 missing_param
  • category_id не найден → 404
  • Сломанная иерархия  → 422 orphan_node
"""

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.db import get_db
from app.schemas.category import (
    BreadcrumbsResponse,
    CategoryDetailResponse,
    CategoryTreeResponse,
)
from app.services import category_service

router = APIRouter(prefix="/api/v1", tags=["Categories"])


@router.get(
    "/categories",
    response_model=CategoryTreeResponse,
    summary="Дерево категорий",
    description=(
        "Возвращает полное дерево категорий. "
        "Orphan node (parent_id ссылается на несуществующую запись) → 422."
    ),
)
async def get_category_tree(
    db: AsyncSession = Depends(get_db),
) -> CategoryTreeResponse:
    return await category_service.get_category_tree(db)


@router.get(
    "/categories/{category_id}",
    response_model=CategoryDetailResponse,
    summary="Детали категории",
)
async def get_category_detail(
    category_id: uuid.UUID,
    include_product_count: bool = Query(False),
    db: AsyncSession = Depends(get_db),
) -> CategoryDetailResponse:
    return await category_service.get_category_detail(
        db,
        category_id=category_id,
        include_product_count=include_product_count,
    )


@router.get(
    "/breadcrumbs",
    response_model=BreadcrumbsResponse,
    summary="Навигационная цепочка (breadcrumbs)",
)
async def get_breadcrumbs(
    category_id: uuid.UUID | None = Query(None),
    product_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> BreadcrumbsResponse:
    if category_id is not None and product_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "ambiguous_param",
                "message": "only one of category_id or product_id must be provided",
            },
        )
    if category_id is None and product_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_param",
                "message": "category_id or product_id must be provided",
            },
        )

    if category_id is not None:
        return await category_service.get_breadcrumbs_by_category(db, category_id)

    return await category_service.get_breadcrumbs_by_product(db, product_id)  # type: ignore[arg-type]