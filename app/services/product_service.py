import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.category import Category
from app.models.product import Product, ProductStatus, SKU
from app.schemas.product import Characteristic, ProductCreate, ProductImageCreate, ProductUpdate, SKUUpdate
from app.services.moderation_client import emit_product_deleted, emit_product_deleted_to_b2c, emit_product_edited



def _slugify(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")
    return slug or "product"


def _characteristics_to_storage(characteristics: list[Characteristic]) -> list[dict[str, Any]]:
    return [
        {"id": str(uuid.uuid4()), "name": ch.name, "value": ch.value}
        for ch in characteristics
    ]


def _images_to_storage(images: list[ProductImageCreate]) -> list[dict[str, Any]]:
    return [
        {"id": str(uuid.uuid4()), "url": image.url, "ordering": image.ordering}
        for image in images
    ]


async def create_product(
    db: AsyncSession,
    data: ProductCreate,
    seller_id: uuid.UUID,
) -> Product:
    result = await db.execute(select(Category).where(Category.id == data.category_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="category_id does not exist",
        )

    now = datetime.now(timezone.utc)

    product = Product(
        seller_id=seller_id,
        title=data.title,
        slug=_slugify(data.title),
        description=data.description,
        category_id=data.category_id,
        characteristics=_characteristics_to_storage(data.characteristics),
        images=_images_to_storage(data.images),
        status=ProductStatus.CREATED,
        deleted=False,
        blocking_reason_id=None,
        blocking_reason=None,
        moderator_comment=None,
        field_reports=[],
        created_at=now,
        updated_at=now,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product


_PRODUCT_LOAD_OPTIONS = [
    selectinload(Product.skus).selectinload(SKU.images_rel),
    selectinload(Product.skus).selectinload(SKU.characteristics_rel),
]


async def get_product(
    db: AsyncSession,
    product_id: uuid.UUID,
    *,
    seller_id: uuid.UUID | None = None,
) -> Product:
    """Загрузка карточки. seller_id задан — IDOR: чужой товар → 404."""
    product = await db.get(Product, product_id, options=_PRODUCT_LOAD_OPTIONS)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )
    if seller_id is not None and product.seller_id != seller_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )
    return product


async def update_product(
    db: AsyncSession,
    product_id: uuid.UUID,
    data: ProductUpdate,
    seller_id: uuid.UUID,
) -> Product:
    """
    Обновление товара.
    HARD_BLOCKED → 403.
    MODERATED/BLOCKED → ON_MODERATION + событие EDITED (повторная модерация).
    """
    product = await get_product(db, product_id, seller_id=seller_id)

    if product.status == ProductStatus.HARD_BLOCKED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify HARD_BLOCKED product",
        )

    needs_remoderation = product.status in (ProductStatus.MODERATED, ProductStatus.BLOCKED)

    product.title = data.title
    product.slug = _slugify(data.title)
    product.description = data.description
    product.category_id = data.category_id
    product.characteristics = _characteristics_to_storage(data.characteristics)
    product.images = _images_to_storage(data.images)
    product.updated_at = datetime.now(timezone.utc)

    if needs_remoderation:
        product.status = ProductStatus.ON_MODERATION

    await db.commit()
    await db.refresh(product)

    # Событие EDITED после коммита (fire-and-forget)
    if needs_remoderation:
        first_sku = product.skus[0] if product.skus else None
        emit_product_edited(
            product_id=product.id,
            seller_id=product.seller_id,
            category_id=product.category_id,
            title=product.title,
            sku_id=first_sku.id if first_sku else product.id,
            price=first_sku.price if first_sku else 0,
        )

    return product


async def delete_product(
    db: AsyncSession,
    product_id: uuid.UUID,
    seller_id: uuid.UUID,
) -> None:
    """
    Мягкое удаление товара (soft delete).
    HARD_BLOCKED → 403, уже удалён → 400, чужой → 404.
    После коммита два fire-and-forget:
      1. DELETED → Moderation (снять с очереди модерации)
      2. PRODUCT_DELETED → B2C (пометить корзины по sku_ids)
    """
    product = await get_product(db, product_id, seller_id=seller_id)

    if product.status == ProductStatus.HARD_BLOCKED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete HARD_BLOCKED product",
        )

    if product.deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product is already deleted",
        )

    sku_ids = [sku.id for sku in product.skus]

    product.deleted = True
    product.updated_at = datetime.now(timezone.utc)

    await db.commit()

    # 1. Событие в Moderation — передаём все обязательные поля сигнатуры
    emit_product_deleted(
        product_id=product.id,
        seller_id=product.seller_id,
        category_id=product.category_id,
        title=product.title,
    )
    # 2. Событие в B2C с sku_ids для пометки корзин
    emit_product_deleted_to_b2c(
        product_id=product.id,
        sku_ids=sku_ids,
    )


async def list_seller_products(
    db: AsyncSession,
    seller_id: uuid.UUID,
    *,
    limit: int = 20,
    offset: int = 0,
    status: ProductStatus | None = None,
    include_deleted: bool = False,
    search: str | None = None,
) -> tuple[list[Product], int]:
    conditions = [Product.seller_id == seller_id]

    if not include_deleted:
        conditions.append(Product.deleted.is_(False))

    if status is not None:
        conditions.append(Product.status == status)

    if search:
        pattern = f"%{search}%"
        conditions.append(Product.title.ilike(pattern))

    count_stmt = select(func.count()).select_from(Product).where(*conditions)
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(Product)
        .where(*conditions)
        .options(selectinload(Product.skus))
        .order_by(Product.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    products = list(result.scalars().unique().all())

    return products, total


async def update_sku(
    db: AsyncSession,
    sku_id: uuid.UUID,
    data: SKUUpdate,
    seller_id: uuid.UUID,
) -> "SKU":
    """
    Обновление SKU.
    HARD_BLOCKED товар → 403.
    MODERATED/BLOCKED → ON_MODERATION + событие EDITED.
    Активные резервы (reserved_quantity) сохраняются.
    """
    from sqlalchemy.orm import selectinload
    from app.models.product import SKU, SKUImage, SKUCharacteristic

    result = await db.execute(
        select(SKU)
        .where(SKU.id == sku_id)
        .options(
            selectinload(SKU.images_rel),
            selectinload(SKU.characteristics_rel),
        )
    )
    sku: SKU | None = result.scalar_one_or_none()
    if sku is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SKU not found",
        )

    product = await db.get(Product, sku.product_id, options=_PRODUCT_LOAD_OPTIONS)
    if product is None or product.seller_id != seller_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    if product.status == ProductStatus.HARD_BLOCKED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify SKU of a HARD_BLOCKED product",
        )

    needs_remoderation = product.status in (ProductStatus.MODERATED, ProductStatus.BLOCKED)

    # Обновляем поля SKU (reserved_quantity не трогаем — сохраняем активные резервы)
    sku.name = data.name
    sku.price = data.price
    sku.discount = data.discount
    sku.cost_price = data.cost_price
    sku.article = data.article

    # Заменяем изображения
    for img in list(sku.images_rel):
        await db.delete(img)
    sku.images_rel.clear()
    for img in data.images:
        sku.images_rel.append(SKUImage(sku_id=sku.id, url=img.url, ordering=img.ordering))

    # Заменяем характеристики
    for ch in list(sku.characteristics_rel):
        await db.delete(ch)
    sku.characteristics_rel.clear()
    for ch in data.characteristics:
        sku.characteristics_rel.append(
            SKUCharacteristic(sku_id=sku.id, name=ch.name, value=ch.value)
        )

    if needs_remoderation:
        product.status = ProductStatus.ON_MODERATION

    await db.commit()

    # Перезагружаем с relations
    result2 = await db.execute(
        select(SKU)
        .where(SKU.id == sku_id)
        .options(
            selectinload(SKU.images_rel),
            selectinload(SKU.characteristics_rel),
        )
    )
    sku = result2.scalar_one()

    if needs_remoderation:
        emit_product_edited(
            product_id=product.id,
            seller_id=product.seller_id,
            category_id=product.category_id,
            title=product.title,
            sku_id=sku.id,
            price=sku.price,
        )

    return sku
