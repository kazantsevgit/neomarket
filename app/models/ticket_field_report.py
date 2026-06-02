import uuid

from sqlalchemy import Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class TicketFieldReport(Base):
    __tablename__ = "ticket_field_reports"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id  = Column(UUID(as_uuid=True), ForeignKey("tickets.id"), nullable=False, index=True)
    field_name = Column(String, nullable=False)
    sku_id     = Column(UUID(as_uuid=True), nullable=True)
    comment    = Column(String, nullable=False)

    ticket = relationship("Ticket", back_populates="field_reports")
