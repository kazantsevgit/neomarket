import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.product import Product, ProductStatus
from app.schemas.product import ProductCreate, ProductImageCreate


def _slugify(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")
    return slug or "product"


def _attributes_to_characteristics(attributes: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"id": str(uuid.uuid4()), "name": name, "value": str(value)}
        for name, value in attributes.items()
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
        characteristics=_attributes_to_characteristics(data.attributes or {}),
        images=_images_to_storage(data.images),
        status=ProductStatus.CREATED,
        deleted=False,
        blocking_reason_id=None,
        moderator_comment=None,
        created_at=now,
        updated_at=now,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product
