import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.db import get_db
from app.models.product import Product, ProductStatus
from app.services.product_presenter import product_to_catalog_detail

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])


@router.get(
    "/products/{product_id}",
    summary="Карточка товара (публичная)",
)
async def get_catalog_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(Product, product_id)
    if product is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"code": "NOT_FOUND", "message": "Product not found"},
        )

    if product.deleted or product.status != ProductStatus.MODERATED:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"code": "NOT_FOUND", "message": "Product not found"},
        )

    return product_to_catalog_detail(product)
