"""B2B-7: каталог для B2C — GET /api/v1/products с X-Service-Key."""
from __future__ import annotations

import uuid

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.product import Product, ProductStatus, SKU

_CATALOG_LOAD_OPTIONS = [
    selectinload(Product.skus).selectinload(SKU.images_rel),
    selectinload(Product.skus).selectinload(SKU.characteristics_rel),
]

_HAS_IN_STOCK_SKU = exists(
    select(SKU.id).where(
        SKU.product_id == Product.id,
        SKU.stock_quantity > SKU.reserved_quantity,
    )
)


def _sku_active_quantity(sku: SKU) -> int:
    return max(0, sku.stock_quantity - sku.reserved_quantity)


def is_catalog_visible(product: Product) -> bool:
    """Правила видимости B2B-7 (для тестов и согласованности с SQL-фильтром)."""
    if product.deleted or product.status != ProductStatus.MODERATED:
        return False
    return any(_sku_active_quantity(sku) > 0 for sku in product.skus)


def _catalog_base_conditions():
    return and_(
        Product.status == ProductStatus.MODERATED,
        Product.deleted.is_(False),
        _HAS_IN_STOCK_SKU,
    )


async def list_catalog_products(
    db: AsyncSession,
    *,
    limit: int = 20,
    offset: int = 0,
    category_id: uuid.UUID | None = None,
    search: str | None = None,
    product_ids: list[uuid.UUID] | None = None,
) -> tuple[list[Product], int]:
    conditions = [_catalog_base_conditions()]

    if category_id is not None:
        conditions.append(Product.category_id == category_id)

    if search:
        pattern = f"%{search}%"
        conditions.append(
            or_(Product.title.ilike(pattern), Product.description.ilike(pattern))
        )

    if product_ids is not None:
        conditions.append(Product.id.in_(product_ids))

    where_clause = and_(*conditions)

    count_stmt = select(func.count()).select_from(
        select(Product.id).where(where_clause).subquery()
    )
    total = (await db.execute(count_stmt)).scalar_one()

    list_stmt = (
        select(Product)
        .where(where_clause)
        .options(*_CATALOG_LOAD_OPTIONS)
        .order_by(Product.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(list_stmt)
    products = list(result.scalars().unique().all())
    return products, total
