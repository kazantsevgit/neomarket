import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ModerationEventIdempotency(Base):
    __tablename__ = "moderation_event_idempotency"

    idempotency_key = Column(UUID(as_uuid=True), primary_key=True)
    product_id      = Column(UUID(as_uuid=True), nullable=False, index=True)
    event_type      = Column(String, nullable=False)  # MODERATED | BLOCKED
    created_at      = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
