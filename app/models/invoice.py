import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Invoice(Base):
    __tablename__ = "invoices"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id  = Column(UUID(as_uuid=True), nullable=False, index=True)
    status     = Column(String(50), nullable=False, default="PENDING")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    accepted_by = Column(UUID(as_uuid=True), nullable=True)

    items = relationship("InvoiceItem", back_populates="invoice", lazy="selectin",
                         cascade="all, delete-orphan")


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id        = Column(UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False, index=True)
    sku_id            = Column(UUID(as_uuid=True), ForeignKey("skus.id"), nullable=False)
    sku_name          = Column(String(255), nullable=False)
    quantity          = Column(Integer, nullable=False)
    accepted_quantity = Column(Integer, nullable=True)

    invoice = relationship("Invoice", back_populates="items")
