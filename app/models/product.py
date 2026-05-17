import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum as SAEnum, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class ProductStatus(str, enum.Enum):
    CREATED = "CREATED"
    PENDING_MODERATION = "PENDING_MODERATION"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Product(Base):
    __tablename__ = "products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    category_id = Column(UUID(as_uuid=True), nullable=False)
    characteristics = Column(JSON, nullable=False, default=list)
    images = Column(JSON, nullable=False)
    status = Column(SAEnum(ProductStatus), nullable=False, default=ProductStatus.CREATED)
    deleted = Column(Boolean, nullable=False, default=False)
    blocking_reason_id = Column(UUID(as_uuid=True), nullable=True)
    moderator_comment = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
