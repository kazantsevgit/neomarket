"""
Сервис навигации по категориям (US-CAT-05).

Все три операции работают с плоским списком Category, загружаемым одним SELECT,
и строят нужные структуры в памяти — без повторных SQL-запросов к БД.

Обнаружение orphan node:
  При загрузке проверяем, что каждый parent_id присутствует в множестве id.
  Если нет — категория «оторвана» от дерева → 422 ORPHAN_NODE.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.product import Product, ProductStatus
from app.schemas.category import (
    BreadcrumbItem,
    BreadcrumbMeta,
    BreadcrumbsResponse,
    CategoryDetailResponse,
    CategoryParentRef,
    CategoryTreeItem,
    CategoryTreeResponse,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

async def _load_all(db: AsyncSession) -> list[Category]:
    """Загрузить все категории одним запросом."""
    result = await db.execute(select(Category))
    return list(result.scalars().all())


def _check_orphans(cats: list[Category]) -> None:
    """
    Проверить целостность иерархии.

    Orphan: parent_id не равен NULL, но нет записи с таким id.
    Поднимает 422 при обнаружении.
    """
    ids = {c.id for c in cats}
    for cat in cats:
        if cat.parent_id is not None and cat.parent_id not in ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "orphan_node",
                    "message": "category hierarchy is broken",
                },
            )


def _build_tree(cats: list[Category]) -> list[CategoryTreeItem]:
    """
    Собрать дерево из плоского списка.

    Возвращает корневые узлы с вложенными children.
    """
    nodes: dict[uuid.UUID, CategoryTreeItem] = {
        c.id: CategoryTreeItem(
            id=c.id,
            name=c.name,
            parent_id=c.parent_id,
        )
        for c in cats
    }
    roots: list[CategoryTreeItem] = []
    for cat in cats:
        node = nodes[cat.id]
        if cat.parent_id is None:
            roots.append(node)
        else:
            parent = nodes.get(cat.parent_id)
            if parent is not None:
                parent.children.append(node)
    return roots


def _ancestors(
    cat_id: uuid.UUID,
    by_id: dict[uuid.UUID, Category],
) -> list[Category]:
    """
    Вернуть цепочку предков от корня до cat_id (включительно).

    Обнаруживает цикл: если глубина > len(by_id) — останавливается.
    """
    chain: list[Category] = []
    current_id: uuid.UUID | None = cat_id
    visited: set[uuid.UUID] = set()
    while current_id is not None:
        if current_id in visited:
            break  # защита от циклических ссылок
        visited.add(current_id)
        node = by_id.get(current_id)
        if node is None:
            break
        chain.append(node)
        current_id = node.parent_id
    chain.reverse()  # корень → текущий
    return chain


def _build_url(chain: list[Category]) -> list[str]:
    """Построить URL-сегменты для каждого уровня: /catalog/slug1/slug2/..."""
    segments: list[str] = []
    for cat in chain:
        slug = cat.slug or str(cat.id)
        prefix = segments[-1] if segments else "/catalog"
        segments.append(f"{prefix}/{slug}")
    return segments


# ─── публичные функции ────────────────────────────────────────────────────────

async def get_category_tree(db: AsyncSession) -> CategoryTreeResponse:
    cats = await _load_all(db)
    _check_orphans(cats)
    return CategoryTreeResponse(items=_build_tree(cats))


async def get_category_detail(
    db: AsyncSession,
    category_id: uuid.UUID,
    include_product_count: bool = False,
) -> CategoryDetailResponse:
    # загрузить конкретную категорию
    cat: Category | None = await db.get(Category, category_id)
    if cat is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Category not found"},
        )

    # проверить orphan: загрузить только parent
    parent_obj: Category | None = None
    if cat.parent_id is not None:
        parent_obj = await db.get(Category, cat.parent_id)
        if parent_obj is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "orphan_node",
                    "message": "category hierarchy is broken",
                },
            )

    product_count: int | None = None
    if include_product_count:
        from sqlalchemy import func
        cnt_result = await db.execute(
            select(func.count(Product.id)).where(
                Product.category_id == category_id,
                Product.deleted.is_(False),
                Product.status == ProductStatus.MODERATED,
            )
        )
        product_count = cnt_result.scalar_one()

    parent_ref: CategoryParentRef | None = None
    if parent_obj is not None:
        parent_ref = CategoryParentRef(
            id=parent_obj.id,
            name=parent_obj.name,
            slug=parent_obj.slug,
        )

    created_str = cat.created_at.isoformat() if cat.created_at else None
    updated_str = cat.updated_at.isoformat() if cat.updated_at else None

    return CategoryDetailResponse(
        id=cat.id,
        name=cat.name,
        slug=cat.slug,
        description=cat.description,
        parent=parent_ref,
        product_count=product_count,
        seo=cat.seo,
        meta_tags=cat.meta_tags,
        image_url=cat.image_url,
        is_active=cat.is_active,
        created_at=created_str,
        updated_at=updated_str,
    )


async def get_breadcrumbs_by_category(
    db: AsyncSession,
    category_id: uuid.UUID,
) -> BreadcrumbsResponse:
    cats = await _load_all(db)
    by_id: dict[uuid.UUID, Category] = {c.id: c for c in cats}

    if category_id not in by_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Category not found"},
        )

    _check_orphans(cats)

    chain = _ancestors(category_id, by_id)
    urls = _build_url(chain)

    items = [
        BreadcrumbItem(
            id=node.id,
            slug=node.slug,
            name=node.name,
            url=urls[i],
            level=i,
            is_current=(node.id == category_id),
        )
        for i, node in enumerate(chain)
    ]

    return BreadcrumbsResponse(
        data=items,
        meta=BreadcrumbMeta(
            resolved_via="category_id",
            category_id=category_id,
        ),
    )


async def get_breadcrumbs_by_product(
    db: AsyncSession,
    product_id: uuid.UUID,
) -> BreadcrumbsResponse:
    product: Product | None = await db.get(Product, product_id)
    if product is None or product.deleted or product.status != ProductStatus.MODERATED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Product not found"},
        )

    return await get_breadcrumbs_by_category(db, product.category_id)