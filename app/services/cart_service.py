"""
Cart service: US-CART-03 (гость + авторизованный).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cart import CartItem as CartItemDB
from app.schemas.cart import (
    CartItemAddRequest,
    CartItemUpdateRequest,
    CartResponse,
    CartItem as CartItemResponse,
)
from app.services import b2b_client


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _invalid_cart_identity(message: str = "Cart identity required") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "INVALID_REQUEST", "message": message},
    )


def _get_active_quantity(sku_dict: dict[str, Any]) -> int:
    # B2B обычно отдаёт active_quantity, но на всякий случай поддержим подмену.
    if "active_quantity" in sku_dict and sku_dict["active_quantity"] is not None:
        return int(sku_dict["active_quantity"])
    stock = int(sku_dict.get("stock_quantity", 0) or 0)
    reserved = int(sku_dict.get("reserved_quantity", 0) or 0)
    return max(0, stock - reserved)


def _unavailable_reason_for_cart_item(
    *,
    cart_quantity: int,
    sku_dict: dict[str, Any] | None,
) -> tuple[bool, int, Optional[str]]:
    """
    Возвращает: (is_available, available_quantity, unavailable_reason).
    reason вычисляется на каждом GET /cart (не хранится в БД).
    """
    if not sku_dict or sku_dict.get("_not_found"):
        # SKU исчез — считаем как удалён/дизейблнутый товар.
        return False, 0, "PRODUCT_DELETED"

    product = sku_dict.get("product") or {}
    product_deleted = bool(product.get("deleted", False))
    product_status = product.get("status")

    available_qty = _get_active_quantity(sku_dict)

    if product_deleted:
        return False, available_qty, "PRODUCT_DELETED"

    # Заблокированный/не прощёл модерацию товар.
    if product_status in ("BLOCKED", "HARD_BLOCKED", "REJECTED") or (
        product_status not in ("MODERATED", "PUBLISHED", None)
    ):
        # Для он-модерации даём отдельный текст причины.
        if product_status == "ON_MODERATION":
            return False, available_qty, "ON_MODERATION"
        return False, available_qty, "PRODUCT_BLOCKED"

    # Остаток:
    if available_qty <= 0:
        return False, available_qty, "OUT_OF_STOCK"

    if available_qty < cart_quantity:
        return False, available_qty, "QUANTITY_REDUCED"

    return True, available_qty, None


def _b2b_sku_to_cart_item(
    *,
    cart_item: CartItemDB,
    sku_dict: dict[str, Any] | None,
    computed_unit_price: int,
) -> CartItemResponse:
    product = (sku_dict or {}).get("product") or {}
    product_id = uuid.UUID(str(product.get("id"))) if product.get("id") else uuid.uuid4()
    product_title = str(product.get("title") or "")

    sku_name = str((sku_dict or {}).get("name") or "")
    name = (product_title + " " + sku_name).strip() or sku_name or product_title or str(cart_item.sku_id)
    sku_code = (sku_dict or {}).get("article") or (sku_dict or {}).get("sku_code")

    is_available, available_quantity, unavailable_reason = _unavailable_reason_for_cart_item(
        cart_quantity=cart_item.quantity,
        sku_dict=sku_dict,
    )

    unit_price_at_add = getattr(cart_item, "unit_price_at_add", None)
    line_total = computed_unit_price * cart_item.quantity if is_available else 0

    image = None
    # Если B2B отдаёт images: [{url, ordering}] — берём первую подходящую.
    images = (sku_dict or {}).get("images") or (sku_dict or {}).get("image") or []
    if isinstance(images, list) and images:
        try:
            first = min(images, key=lambda im: int(im.get("ordering", 0)))
            image = {"url": first.get("url"), "ordering": int(first.get("ordering", 0))}
        except Exception:
            image = None

    return CartItemResponse(
        sku_id=cart_item.sku_id,
        product_id=product_id,
        name=name,
        sku_code=sku_code,
        quantity=cart_item.quantity,
        unit_price=computed_unit_price,
        unit_price_at_add=unit_price_at_add,
        line_total=line_total,
        available_quantity=available_quantity,
        is_available=is_available,
        unavailable_reason=unavailable_reason,
        image=image,
    )


async def _get_cart_items(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    session_id: uuid.UUID | None,
) -> list[CartItemDB]:
    if user_id is not None:
        stmt = select(CartItemDB).where(CartItemDB.user_id == user_id)
    else:
        stmt = select(CartItemDB).where(CartItemDB.session_id == session_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _get_cart_item_by_sku(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    session_id: uuid.UUID | None,
    sku_id: uuid.UUID,
) -> Optional[CartItemDB]:
    if user_id is not None:
        stmt = select(CartItemDB).where(
            CartItemDB.user_id == user_id,
            CartItemDB.sku_id == sku_id,
        )
    else:
        stmt = select(CartItemDB).where(
            CartItemDB.session_id == session_id,
            CartItemDB.sku_id == sku_id,
        )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _enrich_cart(
    db: AsyncSession,
    *,
    cart_items: list[CartItemDB],
    owner_id: uuid.UUID,
    owner_type: str,
) -> CartResponse:
    sku_ids = [item.sku_id for item in cart_items]

    if not sku_ids:
        return CartResponse(
            id=owner_id,
            items=[],
            items_count=0,
            subtotal=0,
            is_valid=True,
            updated_at=_utcnow(),
        )

    try:
        sku_data = await b2b_client.get_products_by_sku_ids(sku_ids)
    except b2b_client.B2BUnavailableError:
        # Для MVP: считаем, что B2B недоступен — отдаём пустую корзину как временное решение.
        # (Checkout уже имеет гарантированный 503 по контракту.)
        return CartResponse(
            id=owner_id,
            items=[],
            items_count=sum(i.quantity for i in cart_items),
            subtotal=0,
            is_valid=False,
            updated_at=_utcnow(),
        )

    sku_index = {str(s.get("id")): s for s in sku_data}
    enriched_items: list[CartItemResponse] = []
    subtotal = 0
    is_valid = True

    for item in cart_items:
        sku_dict = sku_index.get(str(item.sku_id))
        unit_price = int((sku_dict or {}).get("price") or 0)
        enriched_item = _b2b_sku_to_cart_item(
            cart_item=item,
            sku_dict=sku_dict,
            computed_unit_price=unit_price,
        )
        if not enriched_item.is_available:
            is_valid = False
        subtotal += enriched_item.line_total
        enriched_items.append(enriched_item)

    return CartResponse(
        id=owner_id,
        items=enriched_items,
        items_count=sum(i.quantity for i in cart_items),
        subtotal=subtotal,
        is_valid=is_valid,
        updated_at=_utcnow(),
    )


async def get_cart_enriched(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    session_id: uuid.UUID | None,
) -> CartResponse:
    if user_id is None and session_id is None:
        raise _invalid_cart_identity()

    cart_items = await _get_cart_items(db, user_id=user_id, session_id=session_id)
    owner_id = user_id if user_id is not None else session_id  # type: ignore[assignment]
    owner_type = "user" if user_id is not None else "guest"
    return await _enrich_cart(
        db,
        cart_items=cart_items,
        owner_id=owner_id,
        owner_type=owner_type,
    )


async def add_sku_to_cart(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    session_id: uuid.UUID | None,
    sku_id: uuid.UUID,
    quantity: int,
) -> CartResponse:
    if user_id is None and session_id is None:
        raise _invalid_cart_identity()

    # Валидация SKU: SKU должен существовать и быть доступным по остаткам на момент добавления.
    try:
        sku_data = await b2b_client.get_products_by_sku_ids([sku_id])
    except b2b_client.B2BUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "B2B_UNAVAILABLE", "message": "Сервис товаров временно недоступен"},
        )

    sku_dict = sku_data[0] if sku_data else None
    if not sku_dict or sku_dict.get("_not_found"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SKU_NOT_FOUND", "message": "SKU не найден"},
        )

    product = sku_dict.get("product") or {}
    product_deleted = bool(product.get("deleted", False))
    product_status = product.get("status")
    if product_deleted or product_status in ("BLOCKED", "HARD_BLOCKED", "REJECTED"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PRODUCT_BLOCKED", "message": "Товар недоступен"},
        )
    if product_status not in ("MODERATED", "PUBLISHED", "ON_MODERATION", None):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PRODUCT_BLOCKED", "message": "Товар недоступен"},
        )

    active_qty = _get_active_quantity(sku_dict)

    existing = await _get_cart_item_by_sku(
        db,
        user_id=user_id,
        session_id=session_id,
        sku_id=sku_id,
    )

    new_quantity = quantity if existing is None else int(existing.quantity) + int(quantity)
    if active_qty < new_quantity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "INSUFFICIENT_STOCK", "message": "Недостаточно остатков"},
        )

    if existing is not None:
        existing.quantity = new_quantity
        # unit_price_at_add оставляем как был — чтобы подсветить изменение.
    else:
        db.add(
            CartItemDB(
                id=uuid.uuid4(),
                user_id=user_id,
                session_id=session_id,
                sku_id=sku_id,
                quantity=new_quantity,
                unit_price_at_add=int(sku_dict.get("price") or 0),
            )
        )

    await db.commit()

    owner_id = user_id if user_id is not None else session_id  # type: ignore[assignment]
    return await get_cart_enriched(
        db,
        user_id=user_id,
        session_id=session_id,
    )


async def update_cart_item_quantity(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    session_id: uuid.UUID | None,
    sku_id: uuid.UUID,
    quantity: int,
) -> CartResponse:
    if user_id is None and session_id is None:
        raise _invalid_cart_identity()

    # Валидация в B2B — active_quantity должно покрывать новую quantity.
    try:
        sku_data = await b2b_client.get_products_by_sku_ids([sku_id])
    except b2b_client.B2BUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "B2B_UNAVAILABLE", "message": "Сервис товаров временно недоступен"},
        )

    sku_dict = sku_data[0] if sku_data else None
    if not sku_dict or sku_dict.get("_not_found"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SKU_NOT_FOUND", "message": "SKU не найден"},
        )

    product = sku_dict.get("product") or {}
    if bool(product.get("deleted", False)) or product.get("status") in ("BLOCKED", "HARD_BLOCKED", "REJECTED"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PRODUCT_BLOCKED", "message": "Товар недоступен"},
        )

    active_qty = _get_active_quantity(sku_dict)
    if active_qty < quantity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "INSUFFICIENT_STOCK", "message": "Недостаточно остатков"},
        )

    existing = await _get_cart_item_by_sku(
        db,
        user_id=user_id,
        session_id=session_id,
        sku_id=sku_id,
    )
    if existing is None:
        # Если нет позиции — создаём её (идемпотентно по SKU).
        db.add(
            CartItemDB(
                id=uuid.uuid4(),
                user_id=user_id,
                session_id=session_id,
                sku_id=sku_id,
                quantity=quantity,
                unit_price_at_add=int(sku_dict.get("price") or 0),
            )
        )
    else:
        existing.quantity = quantity
    await db.commit()

    return await get_cart_enriched(db, user_id=user_id, session_id=session_id)


async def delete_cart_item(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    session_id: uuid.UUID | None,
    sku_id: uuid.UUID,
) -> CartResponse:
    if user_id is None and session_id is None:
        raise _invalid_cart_identity()

    existing = await _get_cart_item_by_sku(
        db,
        user_id=user_id,
        session_id=session_id,
        sku_id=sku_id,
    )
    if existing is not None:
        await db.delete(existing)
        await db.commit()
    return await get_cart_enriched(db, user_id=user_id, session_id=session_id)


async def clear_cart(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    session_id: uuid.UUID | None,
) -> None:
    if user_id is None and session_id is None:
        raise _invalid_cart_identity()

    items = await _get_cart_items(db, user_id=user_id, session_id=session_id)
    for item in items:
        await db.delete(item)
    await db.commit()


async def merge_guest_into_user(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    guest_session_id: uuid.UUID,
) -> CartResponse:
    """
    Merge guest cart into auth cart:
    - конфликт по sku_id: берём MAX(guest, auth)
    - guest позиции переносим (или удаляем при конфликте)
    """
    guest_items = await _get_cart_items(db, user_id=None, session_id=guest_session_id)
    auth_items = await _get_cart_items(db, user_id=user_id, session_id=None)

    auth_by_sku = {item.sku_id: item for item in auth_items}

    for guest_item in guest_items:
        auth_item = auth_by_sku.get(guest_item.sku_id)
        if auth_item is not None:
            # конфликт: MAX
            if int(guest_item.quantity) > int(auth_item.quantity):
                auth_item.quantity = int(guest_item.quantity)
            # удаляем гостевую запись
            await db.delete(guest_item)
        else:
            # переносим в user_id
            guest_item.user_id = user_id
            guest_item.session_id = None
            auth_by_sku[guest_item.sku_id] = guest_item

    await db.commit()
    return await get_cart_enriched(db, user_id=user_id, session_id=None)


async def validate_cart(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    session_id: uuid.UUID | None,
) -> Any:
    # MVP: валидируем по тому же обогащению, что и GET /cart.
    cart = await get_cart_enriched(db, user_id=user_id, session_id=session_id)
    issues = []
    return {"is_valid": cart.is_valid, "cart": cart, "issues": issues}

