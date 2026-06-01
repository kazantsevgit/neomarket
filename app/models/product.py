import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Enum as SAEnum,
    ForeignKey, Integer, JSON, Numeric, String, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class ProductStatus(str, enum.Enum):
    CREATED = "CREATED"
    ON_MODERATION = "ON_MODERATION"
    MODERATED = "MODERATED"
    BLOCKED = "BLOCKED"
    HARD_BLOCKED = "HARD_BLOCKED"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Product(Base):
    __tablename__ = "products"

    id                 = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id          = Column(UUID(as_uuid=True), nullable=False, index=True)
    title              = Column(String(255), nullable=False)
    slug               = Column(String(255), nullable=False)
    description        = Column(Text, nullable=True)
    category_id        = Column(UUID(as_uuid=True), nullable=False)
    characteristics    = Column(JSON, nullable=False, default=list)
    images             = Column(JSON, nullable=False)
    status             = Column(SAEnum(ProductStatus), nullable=False, default=ProductStatus.CREATED)
    deleted            = Column(Boolean, nullable=False, default=False)
    blocking_reason_id = Column(UUID(as_uuid=True), nullable=True)
    blocking_reason    = Column(JSON, nullable=True)
    moderator_comment  = Column(String, nullable=True)
    field_reports      = Column(JSON, nullable=False, default=list)
    created_at         = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at         = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    skus = relationship("SKU", back_populates="product", lazy="selectin")


class SKUImage(Base):
    __tablename__ = "sku_images"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku_id     = Column(UUID(as_uuid=True), ForeignKey("skus.id"), nullable=False, index=True)
    url        = Column(String, nullable=False)
    ordering   = Column(Integer, nullable=False, default=0)

    sku = relationship("SKU", back_populates="images_rel")


class SKUCharacteristic(Base):
    __tablename__ = "sku_characteristics"

    id     = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku_id = Column(UUID(as_uuid=True), ForeignKey("skus.id"), nullable=False, index=True)
    name   = Column(String, nullable=False)
    value  = Column(String, nullable=False)

    sku = relationship("SKU", back_populates="characteristics_rel")


class SKU(Base):
    __tablename__ = "skus"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id       = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)
    name             = Column(String(255), nullable=False)
    # Деньги в копейках (int), как требует neomarket-b2b.yaml
    price            = Column(Integer, nullable=False)
    discount         = Column(Integer, nullable=False, default=0)
    cost_price       = Column(Integer, nullable=True)
    article          = Column(String, nullable=True)
    stock_quantity   = Column(Integer, nullable=False, default=0)
    reserved_quantity = Column(Integer, nullable=False, default=0)
    created_at       = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at       = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    product             = relationship("Product", back_populates="skus")
    images_rel          = relationship("SKUImage", back_populates="sku", lazy="selectin",
                                       cascade="all, delete-orphan")
    characteristics_rel = relationship("SKUCharacteristic", back_populates="sku", lazy="selectin",
                                       cascade="all, delete-orphan")

    @property
    def active_quantity(self) -> int:
        return max(0, self.stock_quantity - self.reserved_quantity)
