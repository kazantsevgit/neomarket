import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum as SAEnum, Integer, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class ModerationStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    MODERATED = "MODERATED"
    BLOCKED = "BLOCKED"
    HARD_BLOCKED = "HARD_BLOCKED"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProductModeration(Base):
    __tablename__ = "product_moderation"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    seller_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    json_before = Column(JSON, nullable=True)
    json_after = Column(JSON, nullable=True)
    status = Column(SAEnum(ModerationStatus), nullable=False, default=ModerationStatus.PENDING)
    queue_priority = Column(Integer, nullable=False, default=1)
    moderator_id = Column(UUID(as_uuid=True), nullable=True)
    date_created = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    date_updated = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    field_reports = relationship("FieldReport", back_populates="product_moderation", cascade="all, delete-orphan")
