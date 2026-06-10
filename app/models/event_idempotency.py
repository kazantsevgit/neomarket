import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventIdempotencyKey(Base):
    __tablename__ = "event_idempotency_keys"

    idempotency_key: Mapped[uuid.UUID] = Column(UUID(as_uuid=True), primary_key=True)
    event: Mapped[str] = Column(String, nullable=False)
    product_id: Mapped[uuid.UUID] = Column(UUID(as_uuid=True), nullable=False, index=True)
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
