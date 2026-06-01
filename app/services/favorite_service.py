import uuid

from fastapi import HTTPException, status
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.favorite import Favorite
from app.models.product import Product, ProductStatus, SKU
from app.schemas.favorite import (
    CatalogProductCard,
    CategoryRef,
    ImageRef,
    PaginatedCatalogProducts,
)
from app.models.category import Category


async def add_favorite(
    db: AsyncSession,
    user_id: uuid.UUID,
    product_id: uuid.UUID,
) -> bool:
    product = await db.get(Product, product_id)
    if product is None or product.deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    existing = await db.get(Favorite, (user_id, product_id))
    if existing is not None:
        return False

    favorite = Favorite(user_id=user_id, product_id=product_id)
    db.add(favorite)
    await db.commit()
    return True


async def remove_favorite(
    db: AsyncSession,
    user_id: uuid.UUID,
    product_id: uuid.UUID,
) -> None:
    await db.execute(
        sa_delete(Favorite).where(
            Favorite.user_id == user_id,
            Favorite.product_id == product_id,
        )
    )
    await db.commit()


async def get_favorites(
    db: AsyncSession,
    user_id: uuid.UUID,
    limit: int = 20,
    offset: int = 0,
) -> PaginatedCatalogProducts:
    visible_statuses = [
        ProductStatus.CREATED,
        ProductStatus.ON_MODERATION,
        ProductStatus.MODERATED,
    ]

    count_q = (
        select(func.count(Favorite.user_id))
        .select_from(Favorite)
        .join(Product, Favorite.product_id == Product.id)
        .where(
            Favorite.user_id == user_id,
            Product.deleted == False,
            Product.status.in_(visible_statuses),
        )
    )
    total_result = await db.execute(count_q)
    total_count = total_result.scalar() or 0

    fav_q = (
        select(Favorite)
        .where(Favorite.user_id == user_id)
        .order_by(Favorite.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    fav_result = await db.execute(fav_q)
    favorites = fav_result.scalars().all()

    if not favorites:
        return PaginatedCatalogProducts(
            items=[],
            total_count=total_count,
            limit=limit,
            offset=offset,
        )

    product_ids = [f.product_id for f in favorites]

    prod_q = (
        select(Product)
        .where(
            Product.id.in_(product_ids),
            Product.deleted == False,
            Product.status.in_(visible_statuses),
        )
        .options(selectinload(Product.skus))
    )
    prod_result = await db.execute(prod_q)
    products = prod_result.scalars().all()

    product_map = {p.id: p for p in products}

    category_ids = list({p.category_id for p in products})
    cat_q = select(Category).where(Category.id.in_(category_ids))
    cat_result = await db.execute(cat_q)
    category_map = {c.id: c for c in cat_result.scalars().all()}

    items: list[CatalogProductCard] = []
    for p in products:
        cat = category_map.get(p.category_id)
        if cat is not None:
            cat_ref = CategoryRef(
                id=cat.id,
                name=cat.name,
                level=0,
                path=[cat.name],
            )
        else:
            cat_ref = CategoryRef(
                id=p.category_id,
                name="",
                level=0,
                path=[],
            )

        min_price = None
        old_price = None
        has_stock = False
        for sku in p.skus:
            effective_price = sku.price
            if min_price is None or effective_price < min_price:
                min_price = effective_price
            if sku.discount > 0:
                old_candidate = sku.price + sku.discount
                if old_price is None or old_candidate > old_price:
                    old_price = old_candidate
            if sku.active_quantity > 0:
                has_stock = True

        images = []
        for img in (p.images or []):
            images.append(ImageRef(
                id=img["id"] if isinstance(img.get("id"), uuid.UUID) else uuid.UUID(img["id"]),
                url=img.get("url", ""),
                alt=img.get("alt"),
                ordering=img.get("ordering", 0),
                is_main=img.get("ordering", 0) == 0,
            ))

        items.append(CatalogProductCard(
            id=p.id,
            name=p.title,
            slug=p.slug,
            category=cat_ref,
            min_price=min_price or 0,
            old_price=old_price,
            has_stock=has_stock,
            rating=None,
            reviews_count=0,
            images=images,
            seller={"id": p.seller_id},
        ))

    return PaginatedCatalogProducts(
        items=items,
        total_count=total_count,
        limit=limit,
        offset=offset,
    )
