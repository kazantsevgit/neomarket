import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Favorite(Base):
    __tablename__ = "favorites"

    user_id = Column(UUID(as_uuid=True), primary_key=True)
    product_id = Column(UUID(as_uuid=True), primary_key=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
