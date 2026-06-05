import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import SKU, ProductStatus
from app.models.invoice import Invoice, InvoiceItem
from app.schemas.invoice import InvoiceCreate, InvoiceResponse
from app.schemas.errors import invalid_request


async def create_invoice(
    db: AsyncSession,
    seller_id: uuid.UUID,
    data: InvoiceCreate,
) -> Invoice:
    if not data.items:
        raise invalid_request("At least one item is required")

    sku_ids = [item.sku_id for item in data.items]

    result = await db.execute(
        select(SKU)
        .where(SKU.id.in_(sku_ids))
        .options(selectinload(SKU.product))
    )
    skus = result.scalars().all()
    sku_map = {s.id: s for s in skus}

    for sku_id in sku_ids:
        if sku_id not in sku_map:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "SKU not found"},
            )

    for sku in skus:
        if sku.product.seller_id != seller_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "NOT_OWNER",
                    "message": "One or more SKUs do not belong to the authenticated seller",
                },
            )

    for sku in skus:
        if sku.product.status != ProductStatus.MODERATED:
            raise invalid_request("Invoice can only be created for MODERATED products")

    for item in data.items:
        if item.quantity <= 0:
            raise invalid_request("quantity must be > 0")

    invoice = Invoice(seller_id=seller_id, status="PENDING")
    db.add(invoice)
    await db.flush()

    for item in data.items:
        sku = sku_map[item.sku_id]
        invoice_item = InvoiceItem(
            invoice_id=invoice.id,
            sku_id=item.sku_id,
            sku_name=sku.name,
            quantity=item.quantity,
            accepted_quantity=None,
        )
        db.add(invoice_item)

    await db.commit()
    await db.refresh(invoice)
    return invoice


def invoice_to_response(invoice: Invoice) -> InvoiceResponse:
    return InvoiceResponse(
        id=invoice.id,
        status=invoice.status,
        created_at=invoice.created_at,
        items=[
            {
                "id": item.id,
                "sku_id": item.sku_id,
                "sku_name": item.sku_name,
                "quantity": item.quantity,
                "accepted_quantity": item.accepted_quantity,
            }
            for item in invoice.items
        ],
    )
