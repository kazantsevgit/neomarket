import uuid

from fastapi import HTTPException, status
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.models.product_subscription import ProductSubscription
from app.schemas.subscription import SubscribeEventType, SubscribeRequest, SubscriptionResponse


async def create_subscription(
    db: AsyncSession,
    user_id: uuid.UUID,
    product_id: uuid.UUID,
    body: SubscribeRequest,
) -> SubscriptionResponse:
    product = await db.get(Product, product_id)
    if product is None or product.deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PRODUCT_NOT_FOUND", "message": "Product not found"},
        )

    existing = await db.execute(
        select(ProductSubscription).where(
            ProductSubscription.user_id == user_id,
            ProductSubscription.product_id == product_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "SUBSCRIPTION_ALREADY_EXISTS",
                "message": "Subscription already exists",
            },
        )

    subscription = ProductSubscription(
        user_id=user_id,
        product_id=product_id,
        events=[e.value for e in body.events],
    )
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    return SubscriptionResponse.model_validate(subscription)


async def delete_subscription(
    db: AsyncSession,
    user_id: uuid.UUID,
    product_id: uuid.UUID,
) -> None:
    await db.execute(
        sa_delete(ProductSubscription).where(
            ProductSubscription.user_id == user_id,
            ProductSubscription.product_id == product_id,
        )
    )
    await db.commit()
