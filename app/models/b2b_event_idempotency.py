import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class B2BEventIdempotency(Base):
    __tablename__ = "b2b_event_idempotency"

    idempotency_key = Column(UUID(as_uuid=True), primary_key=True)
    event_type = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
