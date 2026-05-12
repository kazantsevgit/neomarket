import uuid
from sqlalchemy import Column, String, Text, JSON, Enum as SAEnum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base
import enum


class ProductStatus(str, enum.Enum):
    CREATED = "CREATED"
    PENDING_MODERATION = "PENDING_MODERATION"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"


class Product(Base):
    __tablename__ = "products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    category_id = Column(UUID(as_uuid=True), nullable=False)
    # Характеристики хранятся в JSON-поле (см. ADR в PR)
    attributes = Column(JSON, nullable=False, default=dict)
    images = Column(JSON, nullable=False)  # list of URLs
    status = Column(SAEnum(ProductStatus), nullable=False, default=ProductStatus.CREATED)