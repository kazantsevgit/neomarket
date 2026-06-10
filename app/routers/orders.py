"""
Роутер B2C Orders.

Endpoints:
  POST /api/v1/orders          — Checkout (создание заказа)
  POST /api/v1/orders/{id}/cancel — Отмена заказа

Аутентификация: Bearer JWT.
user_id берётся исключительно из JWT claims (IDOR-защита).
"""

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_seller_id  # reuse — здесь user_id
from app.dependencies.db import get_db
from app.dependencies.service_key import require_catalog_service_key
from app.schemas.orders import CheckoutRequest, OrderResponse
from app.services.order_service import create_order
from app.services.cancel_service import cancel_order
from app.services.deliver_service import deliver_order

import uuid

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def checkout(
    body: CheckoutRequest,
    idempotency_key: uuid.UUID = Header(..., alias="Idempotency-Key"),
    user_id: uuid.UUID = Depends(get_current_seller_id),
    db: AsyncSession = Depends(get_db),
) -> OrderResponse:
    """
    POST /api/v1/orders — Оформление заказа (checkout).

    Состав заказа берётся из корзины покупателя.
    Идемпотентен по заголовку Idempotency-Key: повторный запрос возвращает
    существующий заказ (статус 201, тот же объект).
    """
    return await create_order(
        db=db,
        user_id=user_id,
        idempotency_key=idempotency_key,
        payload=body,
    )


@router.post(
    "/{order_id}/cancel",
    response_model=OrderResponse,
    status_code=status.HTTP_200_OK,
)
async def cancel(
    order_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_seller_id),
    db: AsyncSession = Depends(get_db),
) -> OrderResponse:
    """
    POST /api/v1/orders/{id}/cancel — Отмена заказа.

    Допустимые статусы: CREATED, PAID.
    Если unreserve в B2B упал — статус CANCEL_PENDING, retry асинхронно.
    Чужой заказ → 404 (не 403, IDOR-защита).
    """
    return await cancel_order(db=db, order_id=order_id, user_id=user_id)


@router.post(
    "/{order_id}/deliver",
    response_model=OrderResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_catalog_service_key)],
)
async def deliver(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> OrderResponse:
    """
    POST /api/v1/orders/{id}/deliver — Отметить заказ как доставленный.

    Оператор (B2C Admin) отмечает заказ доставленным → B2C вызывает
    POST /api/v1/inventory/fulfill → B2B для списания резерва.

    Допустимый статус для перехода: DELIVERING.
    Если fulfill в B2B упал — заказ остаётся DELIVERED, retry асинхронно.
    Аутентификация: X-Service-Key (admin/internal).
    """
    return await deliver_order(db=db, order_id=order_id)