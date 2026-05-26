import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.category import Category
from app.models.product import Product, ProductStatus, SKU
from app.schemas.product import Characteristic, ProductCreate, ProductImageCreate, ProductUpdate



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
    """Обновление товара с проверкой HARD_BLOCKED."""
    product = await get_product(db, product_id, seller_id=seller_id)

    if product.status == ProductStatus.HARD_BLOCKED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify HARD_BLOCKED product",
        )

    product.title = data.title
    product.slug = _slugify(data.title)
    product.description = data.description
    product.category_id = data.category_id
    product.characteristics = _characteristics_to_storage(data.characteristics)
    product.images = _images_to_storage(data.images)
    product.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(product)
    return product


async def delete_product(
    db: AsyncSession,
    product_id: uuid.UUID,
    seller_id: uuid.UUID,
) -> None:
    """Мягкое удаление товара с проверкой HARD_BLOCKED."""
    product = await get_product(db, product_id, seller_id=seller_id)

    if product.status == ProductStatus.HARD_BLOCKED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete HARD_BLOCKED product",
        )

    product.deleted = True
    product.updated_at = datetime.now(timezone.utc)

    await db.commit()
