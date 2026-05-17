import uuid

from fastapi import HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product, ProductStatus, SKU, SKUImage, SKUCharacteristic
from app.schemas.product import SKUCreate
from app.services.moderation_client import emit_product_created


async def add_sku(
    db: AsyncSession,
    data: SKUCreate,
    seller_id: uuid.UUID,
) -> SKU:
    # 1. Загружаем товар и проверяем владельца.
    #    Намеренно объединяем "не найден" и "чужой" в один 404 — IDOR-защита
    #    (не раскрываем чужие product_id).
    product: Product | None = await db.get(Product, data.product_id)
    if product is None or product.seller_id != seller_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    # 2. HARD_BLOCKED — SKU добавлять нельзя
    if product.status == ProductStatus.HARD_BLOCKED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot add SKU to a HARD_BLOCKED product",
        )

    # 3. Считаем существующие SKU ДО создания нового
    existing_count_result = await db.execute(
        select(func.count()).where(SKU.product_id == product.id)
    )
    is_first_sku = existing_count_result.scalar_one() == 0

    # 4. Создаём SKU
    sku = SKU(
        product_id=product.id,
        name=data.name,
        price=data.price,
        discount=data.discount,
        cost_price=data.cost_price,
        article=data.article,
    )
    db.add(sku)
    await db.flush()  # получаем sku.id до создания дочерних записей

    # 5. Изображения и характеристики (дочерние строки)
    for img in data.images:
        db.add(SKUImage(sku_id=sku.id, url=img.url, ordering=img.ordering))

    for ch in data.characteristics:
        db.add(SKUCharacteristic(sku_id=sku.id, name=ch.name, value=ch.value))

    # 6. Первый SKU → переводим товар в ON_MODERATION
    if is_first_sku:
        product.status = ProductStatus.ON_MODERATION

    await db.commit()
    await db.refresh(sku)

    # 7. Событие в Moderation — только при первом SKU
    if is_first_sku:
        emit_product_created(
            product_id=product.id,
            seller_id=product.seller_id,
            category_id=product.category_id,
            title=product.title,
            sku_id=sku.id,
            price=sku.price,
        )

    return sku
