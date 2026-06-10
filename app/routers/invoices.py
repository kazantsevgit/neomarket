import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_seller_id
from app.dependencies.db import get_db
from app.schemas.invoice import InvoiceCreate, InvoiceResponse
from app.services.invoice_service import create_invoice, invoice_to_response

router = APIRouter(prefix="/api/v1/invoices", tags=["invoices"])


@router.post("", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice_endpoint(
    body: InvoiceCreate,
    seller_id: uuid.UUID = Depends(get_current_seller_id),
    db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    invoice = await create_invoice(db=db, seller_id=seller_id, data=body)
    return invoice_to_response(invoice)
