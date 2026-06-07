import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import product as product_models
from app.schemas.product import SKUCreate
from app.services.inventory_service import emit_sku_out_of_stock
from app.services.moderation_client import emit_product_created, emit_product_deleted, emit_product_edited

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


async def delete_sku(
    db: AsyncSession,
    sku_id: uuid.UUID,
    seller_id: uuid.UUID,
) -> None:
    """
    Удаление SKU с guardrail-проверками и каскадными side-эффектами.

    Порядок проверок (критичен!):
      1. SKU существует → 404
      2. Ownership (sku.product.seller_id vs JWT seller_id) → 403 NOT_OWNER
      3. Товар HARD_BLOCKED → 403 FORBIDDEN
      4. reserved_quantity > 0 → 409 CONFLICT

    Side-эффекты (после удаления):
      - Если не осталось SKU и товар ON_MODERATION → CREATED + DELETED событие
      - Если active_quantity > 0 и товар MODERATED → SKU_OUT_OF_STOCK событие
    """
    result = await db.execute(
        select(SKU)
        .where(SKU.id == sku_id)
        .options(
            selectinload(SKU.product),
            selectinload(SKU.images_rel),
            selectinload(SKU.characteristics_rel),
        )
    )
    sku = result.scalar_one_or_none()

    if sku is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "SKU not found"},
        )

    product = sku.product

    if product.seller_id != seller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "NOT_OWNER",
                "message": "SKU does not belong to the authenticated seller",
            },
        )

    if product.status == ProductStatus.HARD_BLOCKED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": "Cannot delete SKU of hard-blocked product",
            },
        )

    if sku.reserved_quantity > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CONFLICT",
                "message": "Cannot delete SKU with active reserves",
            },
        )

    # Сохраняем состояние ДО удаления для side-эффектов
    active_quantity = sku.active_quantity
    product_status_before = product.status

    # Физическое удаление SKU
    await db.delete(sku)

    # Считаем оставшиеся SKU после удаления текущего
    remaining_result = await db.execute(
        select(func.count()).where(SKU.product_id == product.id)
    )
    remaining = remaining_result.scalar_one()
    is_last_sku = remaining == 0

    # Side-эффект 1: последний SKU, товар ON_MODERATION → CREATED + DELETED
    if is_last_sku and product.status == ProductStatus.ON_MODERATION:
        product.status = ProductStatus.CREATED
        emit_product_deleted(
            product_id=product.id,
            seller_id=product.seller_id,
            category_id=product.category_id,
            title=product.title,
        )

    # Side-эффект 2: active_quantity > 0 и товар MODERATED → SKU_OUT_OF_STOCK
    if active_quantity > 0 and product_status_before == ProductStatus.MODERATED:
        emit_sku_out_of_stock(sku.id)

    await db.commit()
