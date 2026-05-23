from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.product import Product, ProductStatus, SKU
from app.schemas.catalog import (
    FacetGroup,
    FacetValueCount,
    FacetsResponse,
    ProductShortItem,
    ProductShortListResponse,
)
from app.schemas.errors import VALID_SORTS, invalid_request, invalid_sort_error

# slug фильтра (canon filters[brand]) → имя характеристики в seed-данных
_FILTER_NAME_ALIASES: dict[str, str] = {
    "brand": "бренд",
    "color": "цвет",
    "memory": "объём памяти",
    "original": "оригинальный товар",
}


def _normalize_filter_key(key: str) -> str:
    return key.strip().lower()


def _characteristic_names_for_filter(key: str) -> list[str]:
    normalized = _normalize_filter_key(key)
    names = [normalized]
    alias = _FILTER_NAME_ALIASES.get(normalized)
    if alias:
        names.append(alias)
    return names


def _parse_filters(raw: dict[str, Any] | None) -> dict[str, list[str]]:
    if not raw:
        return {}
    parsed: dict[str, list[str]] = {}
    for key, value in raw.items():
        if value is None:
            continue
        if isinstance(value, list):
            parsed[key] = [str(v) for v in value]
        else:
            parsed[key] = [str(value)]
    return parsed


def _characteristic_match_sql(key: str, value: str):
    names = _characteristic_names_for_filter(key)
    name_conditions = " OR ".join(
        f"lower(elem->>'name') = :name_{i}" for i in range(len(names))
    )
    params: dict[str, Any] = {f"name_{i}": names[i] for i in range(len(names))}
    params["val"] = value
    return text(
        "EXISTS (SELECT 1 FROM json_array_elements(characteristics) AS elem "
        f"WHERE ({name_conditions}) AND elem->>'value' = :val)"
    ).bindparams(**params)


def _in_stock_sku_exists():
    return (
        select(SKU.id)
        .where(
            SKU.product_id == Product.id,
            SKU.stock_quantity > SKU.reserved_quantity,
        )
        .correlate(Product)
        .exists()
    )


def _sku_price_subquery():
    return (
        select(
            SKU.product_id.label("product_id"),
            func.min(SKU.price - SKU.discount).label("min_price"),
            func.max(SKU.discount).label("max_discount"),
        )
        .where(SKU.stock_quantity > SKU.reserved_quantity)
        .group_by(SKU.product_id)
        .subquery()
    )


def _validate_sort(sort: str | None) -> str:
    chosen = sort or "rating"
    if chosen not in VALID_SORTS:
        raise invalid_sort_error()
    return chosen


def _apply_sort(stmt: Select, sort: str, price_sq) -> Select:
    if sort == "price_asc":
        return stmt.order_by(price_sq.c.min_price.asc())
    if sort == "price_desc":
        return stmt.order_by(price_sq.c.min_price.desc())
    if sort == "date_desc":
        return stmt.order_by(Product.created_at.desc())
    if sort == "discount_desc":
        return stmt.order_by(price_sq.c.max_discount.desc())
    # rating, popularity — MVP: по дате создания
    return stmt.order_by(Product.created_at.desc())


def _cover_image(product: Product) -> str | None:
    if product.images:
        first = min(product.images, key=lambda img: img.get("ordering", 0))
        return first.get("url")
    for sku in product.skus:
        if sku.images_rel and sku.active_quantity > 0:
            return min(sku.images_rel, key=lambda img: img.ordering).url
    return None


def _build_base_stmt(
    *,
    category_id: uuid.UUID | None,
    search: str | None,
    filters: dict[str, list[str]],
    min_price: int | None,
    max_price: int | None,
) -> tuple[Select, Any]:
    price_sq = _sku_price_subquery()
    stmt = (
        select(Product, price_sq.c.min_price)
        .join(price_sq, Product.id == price_sq.c.product_id)
        .where(
            Product.status == ProductStatus.MODERATED,
            Product.deleted.is_(False),
            _in_stock_sku_exists(),
        )
        .options(selectinload(Product.skus).selectinload(SKU.images_rel))
    )

    if category_id is not None:
        stmt = stmt.where(Product.category_id == category_id)

    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            or_(
                Product.title.ilike(pattern),
                Product.description.ilike(pattern),
            )
        )

    if min_price is not None:
        stmt = stmt.where(price_sq.c.min_price >= min_price)
    if max_price is not None:
        stmt = stmt.where(price_sq.c.min_price <= max_price)

    for key, values in filters.items():
        if key in ("price", "price_min", "price_max", "in_stock", "original"):
            continue
        value_conditions = [_characteristic_match_sql(key, v) for v in values]
        if value_conditions:
            stmt = stmt.where(or_(*value_conditions))

    return stmt, price_sq


async def list_catalog_products(
    db: AsyncSession,
    *,
    category_id: uuid.UUID | None = None,
    search: str | None = None,
    filters: dict[str, Any] | None = None,
    sort: str | None = None,
    limit: int = 20,
    offset: int = 0,
    min_price: int | None = None,
    max_price: int | None = None,
) -> ProductShortListResponse:
    if search is not None:
        if len(search) < 3:
            raise invalid_request("Search query must be at least 3 characters")
        if len(search) > 255:
            raise invalid_request("Search query must be at most 255 characters")

    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    chosen_sort = _validate_sort(sort)
    parsed_filters = _parse_filters(filters)

    stmt, price_sq = _build_base_stmt(
        category_id=category_id,
        search=search,
        filters=parsed_filters,
        min_price=min_price,
        max_price=max_price,
    )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = _apply_sort(stmt, chosen_sort, price_sq).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).all()

    items = [
        ProductShortItem(
            id=product.id,
            title=product.title,
            image=_cover_image(product),
            price=int(min_price_val),
            in_stock=True,
            is_in_cart=False,
        )
        for product, min_price_val in rows
    ]

    return ProductShortListResponse(
        items=items,
        total_count=total,
        limit=limit,
        offset=offset,
    )


async def get_catalog_facets(
    db: AsyncSession,
    *,
    category_id: uuid.UUID | None = None,
    filters: dict[str, Any] | None = None,
    search: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
) -> FacetsResponse:
    parsed_filters = _parse_filters(filters)
    stmt, _ = _build_base_stmt(
        category_id=category_id,
        search=search,
        filters=parsed_filters,
        min_price=min_price,
        max_price=max_price,
    )
    result = await db.execute(stmt)
    rows = result.all()

    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for product, _ in rows:
        for item in product.characteristics or []:
            name = str(item.get("name", "")).strip()
            value = str(item.get("value", "")).strip()
            if not name or not value:
                continue
            slug = _normalize_filter_key(name)
            counts[slug][value] += 1

    facets = [
        FacetGroup(
            name=name,
            values=[
                FacetValueCount(value=value, count=count)
                for value, count in sorted(values.items(), key=lambda x: (-x[1], x[0]))
            ],
        )
        for name, values in sorted(counts.items())
    ]

    return FacetsResponse(category_id=category_id, facets=facets)
