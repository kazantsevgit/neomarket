import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, TEXT, UUID

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProductSubscription(Base):
    __tablename__ = "product_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    product_id = Column(UUID(as_uuid=True), nullable=False)
    events = Column(ARRAY(TEXT), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_user_product_subscription"),
    )
