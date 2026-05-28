"""
B2C Cart router: US-CART-03
"""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.dependencies.auth import get_current_seller_id, get_optional_current_seller_id
from app.dependencies.db import get_db
from app.schemas.cart import (
    CartItemAddRequest,
    CartItemUpdateRequest,
    CartResponse,
    CartValidationResponse,
)
from app.services import cart_service


router = APIRouter(prefix="/api/v1/cart", tags=["Cart"])


def _resolve_identity(
    user_id: uuid.UUID | None,
    x_session_id: uuid.UUID | None,
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    if user_id is not None:
        return user_id, None
    if x_session_id is not None:
        return None, x_session_id
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "INVALID_REQUEST", "message": "Cart identity required"},
    )


@router.get("", response_model=CartResponse)
async def get_cart(
    user_id: uuid.UUID | None = Depends(get_optional_current_seller_id),
    x_session_id: uuid.UUID | None = Header(None, alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
) -> CartResponse:
    user_id, session_id = _resolve_identity(user_id, x_session_id)
    return await cart_service.get_cart_enriched(db, user_id=user_id, session_id=session_id)


@router.post("/items", response_model=CartResponse)
async def add_item(
    body: CartItemAddRequest,
    user_id: uuid.UUID | None = Depends(get_optional_current_seller_id),
    x_session_id: uuid.UUID | None = Header(None, alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
) -> CartResponse:
    user_id, session_id = _resolve_identity(user_id, x_session_id)
    return await cart_service.add_sku_to_cart(
        db,
        user_id=user_id,
        session_id=session_id,
        sku_id=body.sku_id,
        quantity=body.quantity,
    )


@router.patch("/items/{sku_id}", response_model=CartResponse)
async def update_item(
    sku_id: uuid.UUID,
    body: CartItemUpdateRequest,
    user_id: uuid.UUID | None = Depends(get_optional_current_seller_id),
    x_session_id: uuid.UUID | None = Header(None, alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
) -> CartResponse:
    user_id, session_id = _resolve_identity(user_id, x_session_id)
    return await cart_service.update_cart_item_quantity(
        db,
        user_id=user_id,
        session_id=session_id,
        sku_id=sku_id,
        quantity=body.quantity,
    )


@router.delete("/items/{sku_id}", response_model=CartResponse)
async def delete_item(
    sku_id: uuid.UUID,
    user_id: uuid.UUID | None = Depends(get_optional_current_seller_id),
    x_session_id: uuid.UUID | None = Header(None, alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
) -> CartResponse:
    user_id, session_id = _resolve_identity(user_id, x_session_id)
    return await cart_service.delete_cart_item(db, user_id=user_id, session_id=session_id, sku_id=sku_id)


@router.delete("")
async def clear_cart(
    user_id: uuid.UUID | None = Depends(get_optional_current_seller_id),
    x_session_id: uuid.UUID | None = Header(None, alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    user_id, session_id = _resolve_identity(user_id, x_session_id)
    await cart_service.clear_cart(db, user_id=user_id, session_id=session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/validate", response_model=CartValidationResponse)
async def validate_cart(
    user_id: uuid.UUID | None = Depends(get_optional_current_seller_id),
    x_session_id: uuid.UUID | None = Header(None, alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
) -> CartValidationResponse:
    user_id, session_id = _resolve_identity(user_id, x_session_id)
    return await cart_service.validate_cart(db, user_id=user_id, session_id=session_id)


@router.post("/merge", response_model=CartResponse)
async def merge_guest(
    guest_session_id: uuid.UUID = Header(..., alias="X-Session-Id"),
    user_id: uuid.UUID = Depends(get_current_seller_id),
    db: AsyncSession = Depends(get_db),
) -> CartResponse:
    # Merge — как в каноне: max(quantity) по sku_id.
    return await cart_service.merge_guest_into_user(
        db,
        user_id=user_id,
        guest_session_id=guest_session_id,
    )

