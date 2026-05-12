import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.product import Product, ProductStatus
from app.schemas.product import ProductCreate


async def create_product(
    db: AsyncSession,
    data: ProductCreate,
    seller_id: uuid.UUID,
) -> Product:
    product = Product(
        seller_id=seller_id,  # ТОЛЬКО из JWT, не из body — защита от IDOR
        title=data.title,
        description=data.description,
        category_id=data.category_id,
        attributes=data.attributes or {},
        images=data.images,
        status=ProductStatus.CREATED,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product