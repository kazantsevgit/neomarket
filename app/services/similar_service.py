"""
US-CAT-04: блок похожих товаров.

Алгоритм (канон b2c-catalog-flows.md#b2c-4-similar-products):
  1. Найти видимые товары из той же категории, исключить текущий товар.
  2. Если в категории < limit — расширить на родительскую категорию.
  3. Вернуть случайную выборку (ORDER BY RANDOM() LIMIT N).

ADR (выбор алгоритма выборки):
  Рассматривались три подхода:
  1. ORDER BY RANDOM() — простейшая случайная выборка прямо в SQL.
     Плюс: один запрос, нулевая сложность. Минус: результат меняется
     при каждом запросе (некonsистентно), при большой таблице RANDOM()
     медленнее полного скана.
  2. По совпадению характеристик — считать пересечение характеристик товара
     и ранжировать кандидатов. Плюс: семантическая близость. Минус: требует
     хранения нормализованных характеристик, сложный JOIN, избыточно для MVP.
  3. Кэш предвычисленных рекомендаций — офлайн считать похожих и хранить в Redis.
     Плюс: мгновенный ответ, консистентный. Минус: инфраструктурная сложность,
     нужен фоновый воркер.

  Выбран вариант 1 (ORDER BY RANDOM()).
  Критерии:
  - Сложность реализации на MVP: один SQL-запрос, нет дополнительных зависимостей.
  - Консистентность: на MVP случайный порядок приемлем; блок «Похожие» —
    вспомогательный, а не основной сценарий. При необходимости можно добавить
    seed на основе product_id для детерминированности без смены интерфейса.
"""

from __future__ import annotations

import uuid

from sqlalchemy import exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.category import Category
from app.models.product import Product, ProductStatus, SKU

_DEFAULT_LIMIT = 8
_MAX_LIMIT = 20

# Условие: есть хотя бы один SKU с ненулевым остатком
_HAS_IN_STOCK_SKU = exists(
    select(SKU.id).where(
        SKU.product_id == Product.id,
        SKU.stock_quantity - SKU.reserved_quantity > 0,
    )
)

_EAGER = [
    selectinload(Product.skus),
]


def _visibility_conditions(
    category_id: uuid.UUID,
    exclude_product_id: uuid.UUID,
):
    return [
        Product.status == ProductStatus.MODERATED,
        Product.deleted.is_(False),
        _HAS_IN_STOCK_SKU,
        Product.category_id == category_id,
        Product.id != exclude_product_id,
    ]


async def get_similar_products(
    db: AsyncSession,
    product_id: uuid.UUID,
    limit: int = _DEFAULT_LIMIT,
) -> tuple[list[Product], int]:
    """
    Вернуть (products, total_count) похожих товаров.

    Fallback: если в той же категории меньше limit товаров,
    расширяем выборку на родительскую категорию.

    Raises:
        None — 404 проверяется в роутере.
    """
    limit = min(limit, _MAX_LIMIT)

    # 1. Загрузить исходный товар
    product: Product | None = await db.get(Product, product_id)
    if product is None or product.deleted or product.status != ProductStatus.MODERATED:
        return None, 0  # type: ignore[return-value]  # сигнал роутеру → 404

    category_id = product.category_id

    # 2. Случайная выборка из той же категории
    conditions = _visibility_conditions(category_id, product_id)
    items, total = await _random_sample(db, conditions, limit)

    # 3. Fallback на родительскую категорию, если товаров мало
    if total < limit:
        parent_category: Category | None = await db.get(Category, category_id)
        if parent_category is not None and parent_category.parent_id is not None:
            parent_conditions = _visibility_conditions(
                parent_category.parent_id, product_id
            )
            # Исключаем уже выбранные
            already_ids = {p.id for p in items}
            extra_items, extra_total = await _random_sample(
                db, parent_conditions, limit - len(items)
            )
            extra_items = [p for p in extra_items if p.id not in already_ids]
            items = items + extra_items
            total = total + extra_total

    return items, total


async def _random_sample(
    db: AsyncSession,
    conditions: list,
    limit: int,
) -> tuple[list[Product], int]:
    count_stmt = select(func.count()).select_from(Product).where(*conditions)
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(Product)
        .where(*conditions)
        .options(*_EAGER)
        .order_by(text("RANDOM()"))
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().unique().all()), total