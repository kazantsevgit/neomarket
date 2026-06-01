"""
B2C Order models — Order и OrderItem.

OrderItem хранит исторический снимок цен и названий на момент покупки:
product_title, sku_name, unit_price — зафиксированы и не меняются
при последующем изменении данных в B2B.

Idempotency_key хранится в таблице orders с UNIQUE-индексом — это
основной механизм защиты от двойного checkout (см. ADR в PR).
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OrderStatus(str, enum.Enum):
    CREATED = "CREATED"
    PAID = "PAID"
    ASSEMBLING = "ASSEMBLING"
    DELIVERING = "DELIVERING"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    CANCEL_PENDING = "CANCEL_PENDING"


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_orders_idempotency_key"),
    )

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id          = Column(UUID(as_uuid=True), nullable=False, index=True)
    status           = Column(SAEnum(OrderStatus), nullable=False, default=OrderStatus.PAID)
    total_amount     = Column(BigInteger, nullable=False, default=0)  # копейки
    delivery_address = Column(Text, nullable=True)
    idempotency_key  = Column(UUID(as_uuid=True), nullable=False, unique=True)
    created_at       = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at       = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    items = relationship(
        "OrderItem", back_populates="order", lazy="selectin", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id      = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True)
    sku_id        = Column(UUID(as_uuid=True), nullable=False)
    product_id    = Column(UUID(as_uuid=True), nullable=False)
    # Исторический снимок — зафиксированы при создании заказа
    product_title = Column(String(255), nullable=False)
    sku_name      = Column(String(255), nullable=False)
    quantity      = Column(Integer, nullable=False)
    unit_price    = Column(BigInteger, nullable=False)  # копейки
    line_total    = Column(BigInteger, nullable=False)  # unit_price * quantity

    order = relationship("Order", back_populates="items")
