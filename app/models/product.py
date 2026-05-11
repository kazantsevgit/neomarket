import uuid
import enum
 
from sqlalchemy import Column, String, Text, JSON, Numeric, Enum as SAEnum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
 
from app.database import Base
 
 
class ProductStatus(str, enum.Enum):
    CREATED = "CREATED"
    ON_MODERATION = "ON_MODERATION"   # первый SKU добавлен → уходит на модерацию
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"
    HARD_BLOCKED = "HARD_BLOCKED"     # заблокирован — SKU добавлять нельзя
 
 
class Product(Base):
    __tablename__ = "products"
 
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    category_id = Column(UUID(as_uuid=True), nullable=False)
    attributes = Column(JSON, nullable=False, default=dict)
    images = Column(JSON, nullable=False)   # list of URLs
    status = Column(SAEnum(ProductStatus), nullable=False, default=ProductStatus.CREATED)
 
    skus = relationship("SKU", back_populates="product", lazy="selectin")
 
 
class SKU(Base):
    __tablename__ = "skus"
 
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)
    price = Column(Numeric(12, 2), nullable=False)
    images = Column(JSON, nullable=False)   # list of URLs, минимум 1
    attributes = Column(JSON, nullable=False, default=dict)  # размер, цвет и т.п.
 
    product = relationship("Product", back_populates="skus")