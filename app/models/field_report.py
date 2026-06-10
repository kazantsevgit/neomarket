import uuid

from sqlalchemy import Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class FieldReport(Base):
    __tablename__ = "field_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_moderation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("product_moderation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field_name = Column(String, nullable=False)
    sku_id = Column(UUID(as_uuid=True), nullable=True)
    comment = Column(String, nullable=False)

    product_moderation = relationship("ProductModeration", back_populates="field_reports")
