"""Сериализация Order ORM → OrderResponse (OpenAPI B2C + flows/b2c-orders-flows)."""

from __future__ import annotations

from app.models.order import Order
from app.schemas.orders import OrderItemResponse, OrderResponse


def order_to_response(order: Order) -> OrderResponse:
    items = [
        OrderItemResponse(
            id=item.id,
            sku_id=item.sku_id,
            product_id=item.product_id,
            name=f"{item.product_title} {item.sku_name}".strip() or item.sku_name,
            product_title=item.product_title,
            sku_name=item.sku_name,
            quantity=item.quantity,
            unit_price=item.unit_price,
            line_total=item.line_total,
        )
        for item in order.items
    ]
    subtotal = sum(item.line_total for item in items)
    return OrderResponse(
        id=order.id,
        buyer_id=order.user_id,
        status=order.status.value if hasattr(order.status, "value") else str(order.status),
        items=items,
        subtotal=subtotal,
        total=order.total_amount,
        delivery_address=order.delivery_address,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )
