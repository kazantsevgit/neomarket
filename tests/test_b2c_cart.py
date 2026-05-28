"""
Тесты US-CART-03: корзина покупателя.

DoD-сценарии (имена тестов строго из задания):
  - add_sku_increments_quantity_if_already_in_cart — повторное добавление увеличивает quantity
  - get_cart_enriched_with_b2b_data — GET /cart обогащает из B2B
  - unavailable_sku_shown_with_reason — недоступный SKU возвращается с reason
  - guest_cart_merged_on_login — merge при конфликте берёт MAX(guest, auth)
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from jose import jwt

from app.main import app
from app.config import settings
from app.dependencies.db import get_db
from app.models.cart import CartItem as CartItemDB


_NOW = datetime.now(timezone.utc)
SKU_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()
GUEST_SESSION_ID = uuid.uuid4()
USER_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "buyer@example.com")


def _make_jwt(user_id: uuid.UUID) -> str:
    return jwt.encode(
        {"sub": str(user_id)},
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


GUEST_HEADERS = {"X-Session-Id": str(GUEST_SESSION_ID)}
AUTH_HEADERS = {"Authorization": f"Bearer {_make_jwt(USER_ID)}"}


def make_cart_item(*, sku_id: uuid.UUID = SKU_ID, quantity: int = 1) -> MagicMock:
    item = MagicMock(spec=CartItemDB)
    item.id = uuid.uuid4()
    item.sku_id = sku_id
    item.quantity = quantity
    item.unit_price_at_add = None
    item.user_id = None
    item.session_id = GUEST_SESSION_ID
    item.updated_at = _NOW
    return item


def make_sku_payload(*, sku_id: uuid.UUID = SKU_ID, price: int = 100_00, active_quantity: int = 10):
    return {
        "id": str(sku_id),
        "product_id": str(PRODUCT_ID),
        "name": "SKU Name",
        "price": price,
        "discount": 0,
        "stock_quantity": active_quantity,
        "active_quantity": active_quantity,
        "article": None,
        "images": [],
        "characteristics": [],
        "product": {
            "id": str(PRODUCT_ID),
            "title": "Product title",
            "status": "MODERATED",
            "deleted": False,
        },
    }


@pytest.fixture(autouse=True)
def override_db():
    fake_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_add_sku_increments_quantity_if_already_in_cart(override_db):
    existing = make_cart_item(quantity=1)
    sku_payload = make_sku_payload(active_quantity=10, price=200_00)

    result_existing = MagicMock()
    result_existing.scalar_one_or_none.return_value = existing

    result_list = MagicMock()
    scalar_proxy = MagicMock()
    scalar_proxy.all.return_value = [existing]
    result_list.scalars.return_value = scalar_proxy

    override_db.execute = AsyncMock(side_effect=[result_existing, result_list])

    with patch(
        "app.services.b2b_client.get_products_by_sku_ids",
        new=AsyncMock(return_value=[sku_payload]),
    ):
        async with await _client() as c:
            resp = await c.post(
                "/api/v1/cart/items",
                json={"sku_id": str(SKU_ID), "quantity": 2},
                headers=GUEST_HEADERS,
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["items_count"] == 3
    assert data["subtotal"] == 200_00 * 3
    assert data["items"][0]["sku_id"] == str(SKU_ID)
    assert data["items"][0]["quantity"] == 3


async def test_get_cart_enriched_with_b2b_data(override_db):
    item = make_cart_item(quantity=2)
    item.session_id = GUEST_SESSION_ID

    result_list = MagicMock()
    scalar_proxy = MagicMock()
    scalar_proxy.all.return_value = [item]
    result_list.scalars.return_value = scalar_proxy
    override_db.execute = AsyncMock(return_value=result_list)

    sku_payload = make_sku_payload(active_quantity=10, price=300_00)

    with patch(
        "app.services.b2b_client.get_products_by_sku_ids",
        new=AsyncMock(return_value=[sku_payload]),
    ):
        async with await _client() as c:
            resp = await c.get("/api/v1/cart", headers=GUEST_HEADERS)

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is True
    assert data["items"][0]["unit_price"] == 300_00
    assert data["items"][0]["available_quantity"] == 10
    assert data["items"][0]["is_available"] is True
    assert data["items"][0]["line_total"] == 300_00 * 2
    assert data["subtotal"] == 300_00 * 2


async def test_unavailable_sku_shown_with_reason(override_db):
    item = make_cart_item(quantity=2)
    item.session_id = GUEST_SESSION_ID

    result_list = MagicMock()
    scalar_proxy = MagicMock()
    scalar_proxy.all.return_value = [item]
    result_list.scalars.return_value = scalar_proxy
    override_db.execute = AsyncMock(return_value=result_list)

    sku_payload = make_sku_payload(active_quantity=0, price=400_00)

    with patch(
        "app.services.b2b_client.get_products_by_sku_ids",
        new=AsyncMock(return_value=[sku_payload]),
    ):
        async with await _client() as c:
            resp = await c.get("/api/v1/cart", headers=GUEST_HEADERS)

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is False
    assert data["items"][0]["is_available"] is False
    assert data["items"][0]["unavailable_reason"] == "OUT_OF_STOCK"
    assert data["items"][0]["line_total"] == 0
    assert data["subtotal"] == 0


async def test_guest_cart_merged_on_login(override_db):
    guest_item = make_cart_item(quantity=3)
    guest_item.user_id = None
    guest_item.session_id = GUEST_SESSION_ID

    auth_item = make_cart_item(quantity=1)
    auth_item.user_id = USER_ID
    auth_item.session_id = None

    result_guest_list = MagicMock()
    scalar_proxy_guest = MagicMock()
    scalar_proxy_guest.all.return_value = [guest_item]
    result_guest_list.scalars.return_value = scalar_proxy_guest

    result_auth_list = MagicMock()
    scalar_proxy_auth = MagicMock()
    scalar_proxy_auth.all.return_value = [auth_item]
    result_auth_list.scalars.return_value = scalar_proxy_auth

    # get_cart_enriched после merge → снова вернём auth_item
    result_after_merge = MagicMock()
    scalar_proxy_after = MagicMock()
    scalar_proxy_after.all.return_value = [auth_item]
    result_after_merge.scalars.return_value = scalar_proxy_after

    override_db.execute = AsyncMock(
        side_effect=[
            result_guest_list,   # merge: guest items
            result_auth_list,    # merge: auth items
            result_after_merge,  # merge: get_cart_enriched inside login
            result_after_merge,  # explicit GET /cart after login
        ]
    )
    override_db.delete = AsyncMock()

    sku_payload = make_sku_payload(active_quantity=10, price=500_00)

    with patch(
        "app.services.b2b_client.get_products_by_sku_ids",
        new=AsyncMock(return_value=[sku_payload]),
    ):
        async with await _client() as c:
            resp = await c.post(
                "/api/v1/auth/login",
                json={"email": "buyer@example.com", "password": "SecurePass123!"},
                headers={"X-Session-Id": str(GUEST_SESSION_ID)},
            )

    assert resp.status_code == 200
    token = resp.json()
    assert token["user_id"] == str(USER_ID)
    assert token["access_token"]

    # После логина merge должен был пройти: проверяем корзину уже с JWT
    async with await _client() as c:
        cart_resp = await c.get("/api/v1/cart", headers={"Authorization": f"Bearer {token['access_token']}"})

    assert cart_resp.status_code == 200
    cart = cart_resp.json()
    assert cart["items"][0]["quantity"] == 3
    assert cart["subtotal"] == 500_00 * 3

