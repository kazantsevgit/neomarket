"""Регистрация ORM-моделей для SQLAlchemy (порядок импорта важен)."""

from app.models.invoice import Invoice, InvoiceItem  # noqa: F401
from app.models.ticket_field_report import TicketFieldReport  # noqa: F401
from app.models.ticket import Ticket, TicketKind, TicketStatus  # noqa: F401
