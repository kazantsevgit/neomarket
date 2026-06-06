import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import product as product_models
from app.schemas.product import SKUCreate
from app.services.moderation_client import emit_product_created, emit_product_edited

Product = product_models.Product
ProductStatus = product_models.ProductStatus
SKU = product_models.SKU
SKUImage = product_models.SKUImage
SKUCharacteristic = product_models.SKUCharacteristic


async def _reload_sku_with_relations(db: AsyncSession, sku_id: uuid.UUID) -> SKU:
    result = await db.execute(
        select(SKU)
        .where(SKU.id == sku_id)
        .options(
            selectinload(SKU.images_rel),
            selectinload(SKU.characteristics_rel),
        )
    )
    return result.scalar_one()


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
    existing_count = existing_count_result.scalar_one()
    is_first_sku = existing_count == 0

    # Первый SKU на CREATED → CREATED-событие и ON_MODERATION
    should_send_created = is_first_sku and product.status == ProductStatus.CREATED
    # Любой SKU на MODERATED/BLOCKED → EDITED-событие и ON_MODERATION (повторная модерация)
    should_send_edited = product.status in (ProductStatus.MODERATED, ProductStatus.BLOCKED)
    should_send_to_moderation = should_send_created or should_send_edited

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
        sku.images_rel.append(SKUImage(url=img.url, ordering=img.ordering))

    for ch in data.characteristics:
        sku.characteristics_rel.append(SKUCharacteristic(name=ch.name, value=ch.value))

    # 6. Переход в ON_MODERATION по канону B2B-2
    if should_send_to_moderation:
        product.status = ProductStatus.ON_MODERATION

    await db.commit()
    sku = await _reload_sku_with_relations(db, sku.id)

    # 7. Событие в Moderation
    if should_send_created:
        emit_product_created(
            product_id=product.id,
            seller_id=product.seller_id,
            category_id=product.category_id,
            title=product.title,
            sku_id=sku.id,
            price=sku.price,
        )
    elif should_send_edited:
        emit_product_edited(
            product_id=product.id,
            seller_id=product.seller_id,
            category_id=product.category_id,
            title=product.title,
            sku_id=sku.id,
            price=sku.price,
        )

    return sku
