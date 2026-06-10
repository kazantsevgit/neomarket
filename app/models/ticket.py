import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum as SAEnum, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class TicketKind(str, enum.Enum):
    CREATE = "CREATE"
    EDIT = "EDIT"


class TicketStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"
    HARD_BLOCKED = "HARD_BLOCKED"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Ticket(Base):
    """Карточка товара в очереди модерации (product_moderation)."""

    __tablename__ = "tickets"

    id                    = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id            = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    seller_id             = Column(UUID(as_uuid=True), nullable=False)
    category_id           = Column(UUID(as_uuid=True), nullable=True)
    kind                  = Column(SAEnum(TicketKind), nullable=False, default=TicketKind.CREATE)
    status                = Column(SAEnum(TicketStatus), nullable=False, default=TicketStatus.PENDING)
    assigned_moderator_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    moderator_comment     = Column(String, nullable=True)
    blocking_reason_id    = Column(UUID(as_uuid=True), nullable=True)
    edit_pending          = Column(Boolean, nullable=False, default=False)
    decision_at           = Column(DateTime(timezone=True), nullable=True)
    created_at            = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at            = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    field_reports = relationship(
        "TicketFieldReport",
        back_populates="ticket",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
