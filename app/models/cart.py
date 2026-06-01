"""
B2C Cart models: CartItem (гостевая + авторизованная корзина).

Cart хранит минимум данных: sku_id + quantity + owner (user_id/session_id).
Все цены/остатки обогащаются из B2B на каждом GET /cart.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, DateTime, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CartItem(Base):
    __tablename__ = "cart_items"

    id: Mapped[uuid.UUID] = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Владелец:
    # - для авторизованных: user_id не NULL, session_id NULL
    # - для гостей: user_id NULL, session_id не NULL
    user_id: Mapped[uuid.UUID | None] = Column(UUID(as_uuid=True), nullable=True, index=True)
    session_id: Mapped[uuid.UUID | None] = Column(UUID(as_uuid=True), nullable=True, index=True)

    # Позиция:
    sku_id: Mapped[uuid.UUID] = Column(UUID(as_uuid=True), nullable=False, index=True)
    quantity: Mapped[int] = Column(Integer, nullable=False, default=1)

    # Цена на момент добавления (для подсветки изменений; сама цена актуализируется на GET).
    unit_price_at_add: Mapped[int | None] = Column(Integer, nullable=True)

    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "user_id IS NOT NULL OR session_id IS NOT NULL",
            name="cart_identity_check",
        ),
        # Уникальность позиции в корзине:
        # - только для авторизованного владельца (user_id не NULL)
        # - только для гостя (session_id не NULL)
        UniqueConstraint("user_id", "sku_id", name="uq_cart_user_sku"),
        UniqueConstraint("session_id", "sku_id", name="uq_cart_session_sku"),
    )

