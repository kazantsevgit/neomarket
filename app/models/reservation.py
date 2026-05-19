import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, JSON, String
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReservationIdempotency(Base):
    __tablename__ = "reservation_idempotency"

    idempotency_key = Column(UUID(as_uuid=True), primary_key=True)
    order_id        = Column(UUID(as_uuid=True), nullable=False, index=True)
    response_payload = Column(JSON, nullable=False)
    created_at       = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
