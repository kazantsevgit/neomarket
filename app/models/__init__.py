"""Регистрация ORM-моделей для SQLAlchemy (порядок импорта важен)."""

from app.models.invoice import Invoice, InvoiceItem, InvoiceStatus  # noqa: F401
from app.models.ticket_field_report import TicketFieldReport  # noqa: F401
from app.models.ticket import Ticket, TicketKind, TicketStatus  # noqa: F401
from app.models.product_subscription import ProductSubscription  # noqa: F401
from app.models.event_idempotency import EventIdempotencyKey  # noqa: F401
