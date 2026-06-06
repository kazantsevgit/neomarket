"""
Сервис отмены заказа.

Реализует canonical flow B2C-11:
  1. Загружаем заказ — ownership check (user_id из JWT). Чужой → 404.
  2. Проверяем статус: допустимы только CREATED и PAID → иначе 409 CANCEL_NOT_ALLOWED.
  3. POST /api/v1/unreserve к B2B.
     - Успех → status = CANCELLED.
     - Таймаут / 5xx → status = CANCEL_PENDING (принимаем намерение, retry асинхронно).
  4. Сохраняем заказ, возвращаем покупателю.

ADR (выбор механизма async retry unreserve):
  Рассматривались три варианта:
  1. Celery task с exponential backoff — надёжно: задача персистируется в брокере
     (Redis/RabbitMQ), выживает при рестарте сервиса, backoff не перегружает B2B.
     Минус: требует настройки брокера и воркера — дополнительная инфраструктура.
  2. Management command по cron (например, через APScheduler или системный cron) —
     проще инфраструктурно: один скрипт и cron-запись. Минус: гранулярность retry
     ограничена частотой запуска cron; при рестарте сервиса в процессе cron-окна
     часть заказов может ждать до следующего тика.
  3. Django Q / aiotask-queue — аналог Celery, но с другим API; сложность примерно
     та же, экосистема меньше.

  Выбран вариант 1: Celery с exponential backoff.
  Критерии:
  - Гарантия выполнения при рестарте: задача в брокере не теряется даже если
    сервис упал — воркер подхватит её после старта.
  - Сложность настройки: выше cron, но Celery уже стандарт в Django/FastAPI-стеке;
    добавление брокера (Redis) оправдано если он уже используется для других задач.

  Первая итерация (scaffold): при CANCEL_PENDING логируем ошибку и оставляем статус.
  Retry-воркер подключается в следующей итерации без изменения контракта.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderStatus
from app.orders.errors import order_http_error
from app.orders.presenter import order_to_response
from app.schemas.orders import OrderResponse
from app.services.b2b_client import B2BUnavailableError, unreserve

logger = logging.getLogger(__name__)

# Статусы, из которых разрешена отмена
_CANCELLABLE = {OrderStatus.CREATED, OrderStatus.PAID}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def cancel_order(
    db: AsyncSession,
    order_id: uuid.UUID,
    user_id: uuid.UUID,
) -> OrderResponse:
    """
    Отменить заказ.

    Raises:
        HTTPException 404 — заказ не найден или принадлежит другому пользователю
        HTTPException 409 — заказ в статусе, который нельзя отменить
    """
    # ── 1. Загрузка + ownership check ─────────────────────────────────────────
    result = await db.execute(select(Order).where(Order.id == order_id))
    order: Order | None = result.scalar_one_or_none()

    if order is None or order.user_id != user_id:
        raise order_http_error(
            status.HTTP_404_NOT_FOUND,
            "ORDER_NOT_FOUND",
            "Заказ не найден",
        )

    # ── 2. Проверка допустимого статуса ──────────────────────────────────────
    if order.status not in _CANCELLABLE:
        raise order_http_error(
            status.HTTP_409_CONFLICT,
            "CANCEL_NOT_ALLOWED",
            f"Отмена невозможна: заказ в статусе {order.status.value}",
            current_status=order.status.value,
        )

    # ── 3. Unreserve → B2B ───────────────────────────────────────────────────
    unreserve_items: List[Dict[str, Any]] = [
        {"sku_id": str(item.sku_id), "quantity": item.quantity}
        for item in order.items
    ]

    try:
        await unreserve(order_id=order.id, items=unreserve_items)
        order.status = OrderStatus.CANCELLED
    except B2BUnavailableError as exc:
        # B2B недоступен — принимаем намерение, retry асинхронно (scaffold)
        logger.error(
            "unreserve failed for order %s, transitioning to CANCEL_PENDING: %s",
            order_id,
            exc,
        )
        order.status = OrderStatus.CANCEL_PENDING

    # ── 4. Сохраняем ─────────────────────────────────────────────────────────
    await db.commit()
    await db.refresh(order)

    return order_to_response(order)
