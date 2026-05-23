"""
Роутер B2C Orders.

Endpoints:
  POST /api/v1/orders  — Checkout (создание заказа)

Аутентификация: Bearer JWT.
user_id берётся исключительно из JWT claims (IDOR-защита).
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_seller_id  # reuse — здесь user_id
from app.dependencies.db import get_db
from app.schemas.orders import CheckoutRequest, OrderResponse
from app.services.order_service import create_order

import uuid

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def checkout(
    body: CheckoutRequest,
    user_id: uuid.UUID = Depends(get_current_seller_id),
    db: AsyncSession = Depends(get_db),
) -> OrderResponse:
    """
    POST /api/v1/orders — Оформление заказа (checkout).

    Идемпотентен по idempotency_key: повторный запрос возвращает
    существующий заказ (статус 201, тот же объект).
    """
    return await create_order(db=db, user_id=user_id, payload=body)
