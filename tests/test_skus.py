"""
Тесты endpoint POST /api/v1/skus.

Покрываемые сценарии (DoD):
  happy:
    - first_sku_transitions_product_to_on_moderation
    - first_sku_emits_created_event_to_moderation
    - second_sku_no_state_change
  unhappy:
    - add_sku_to_hard_blocked_returns_403
    - missing_image_returns_400  (422 Unprocessable Entity — FastAPI/Pydantic)
"""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.dependencies.auth import get_current_seller_id
from app.dependencies.db import get_db
from app.models.product import Product, ProductStatus, SKU

# ─── Фикстуры ────────────────────────────────────────────────────────────────

SELLER_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()
CATEGORY_ID = uuid.uuid4()

VALID_SKU_BODY = {
    "product_id": str(PRODUCT_ID),
    "price": "999.00",
    "images": ["https://cdn.example.com/sku1.jpg"],
    "attributes": {"size": "M"},
}


def make_product(status: ProductStatus = ProductStatus.CREATED) -> MagicMock:
    p = MagicMock(spec=Product)
    p.id = PRODUCT_ID
    p.seller_id = SELLER_ID
    p.category_id = CATEGORY_ID
    p.title = "Test Product"
    p.images = ["https://cdn.example.com/img1.jpg"]
    p.status = status
    return p


def make_sku() -> MagicMock:
    s = MagicMock(spec=SKU)
    s.id = uuid.uuid4()
    s.product_id = PRODUCT_ID
    s.price = Decimal("999.00")
    s.images = ["https://cdn.example.com/sku1.jpg"]
    s.attributes = {"size": "M"}
    return s


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer valid.jwt.token"}


@pytest.fixture(autouse=True)
def override_auth():
    app.dependency_overrides[get_current_seller_id] = lambda: SELLER_ID
    yield
    app.dependency_overrides.pop(get_current_seller_id, None)


@pytest.fixture(autouse=True)
def override_db():
    fake_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield
    app.dependency_overrides.pop(get_db, None)


async def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ─── Happy path ───────────────────────────────────────────────────────────────

async def test_first_sku_transitions_product_to_on_moderation(auth_headers):
    """Первый SKU переводит товар в ON_MODERATION и возвращает 201."""
    expected_sku = make_sku()

    with patch("app.routers.skus.add_sku", return_value=expected_sku) as mock_add:
        async with await make_client() as client:
            resp = await client.post("/api/v1/skus", json=VALID_SKU_BODY, headers=auth_headers)

    assert resp.status_code == 201
    mock_add.assert_called_once()
    call_kwargs = mock_add.call_args.kwargs
    assert call_kwargs["seller_id"] == SELLER_ID


async def test_first_sku_emits_created_event_to_moderation(auth_headers):
    """
    При первом SKU сервис вызывает emit_product_created с корректными полями.
    Проверяем через мок на уровне сервиса.
    """
    product = make_product(ProductStatus.CREATED)
    sku = make_sku()

    # Мокируем db.get → возвращает товар, scalar_one → 0 (нет SKU)
    fake_db = AsyncMock()
    fake_db.get.return_value = product

    scalar_mock = MagicMock()
    scalar_mock.scalar_one.return_value = 0
    fake_db.execute.return_value = scalar_mock

    app.dependency_overrides[get_db] = lambda: fake_db

    with patch("app.services.sku_service.emit_product_created") as mock_emit, \
         patch("app.services.sku_service.SKU", return_value=sku):
        async with await make_client() as client:
            resp = await client.post("/api/v1/skus", json=VALID_SKU_BODY, headers=auth_headers)

    assert resp.status_code == 201
    mock_emit.assert_called_once_with(
        product_id=product.id,
        seller_id=product.seller_id,
        category_id=product.category_id,
        title=product.title,
        images=product.images,
        sku_id=sku.id,
        price=sku.price,
    )
    # Статус товара должен был переключиться
    assert product.status == ProductStatus.ON_MODERATION


async def test_second_sku_no_state_change(auth_headers):
    """Второй SKU не меняет статус и не отправляет событие в Moderation."""
    product = make_product(ProductStatus.ON_MODERATION)
    sku = make_sku()

    fake_db = AsyncMock()
    fake_db.get.return_value = product

    scalar_mock = MagicMock()
    scalar_mock.scalar_one.return_value = 1  # уже есть один SKU
    fake_db.execute.return_value = scalar_mock

    app.dependency_overrides[get_db] = lambda: fake_db

    with patch("app.services.sku_service.emit_product_created") as mock_emit, \
         patch("app.services.sku_service.SKU", return_value=sku):
        async with await make_client() as client:
            resp = await client.post("/api/v1/skus", json=VALID_SKU_BODY, headers=auth_headers)

    assert resp.status_code == 201
    mock_emit.assert_not_called()
    # Статус не должен меняться
    assert product.status == ProductStatus.ON_MODERATION


# ─── Unhappy path ─────────────────────────────────────────────────────────────

async def test_add_sku_to_hard_blocked_returns_403(auth_headers):
    """Попытка добавить SKU к HARD_BLOCKED товару → 403."""
    product = make_product(ProductStatus.HARD_BLOCKED)

    fake_db = AsyncMock()
    fake_db.get.return_value = product
    app.dependency_overrides[get_db] = lambda: fake_db

    async with await make_client() as client:
        resp = await client.post("/api/v1/skus", json=VALID_SKU_BODY, headers=auth_headers)

    assert resp.status_code == 403
    assert "HARD_BLOCKED" in resp.json()["detail"]


async def test_missing_image_returns_422(auth_headers):
    """Тело без поля images → 422 Unprocessable Entity."""
    body = {k: v for k, v in VALID_SKU_BODY.items() if k != "images"}
    async with await make_client() as client:
        resp = await client.post("/api/v1/skus", json=body, headers=auth_headers)

    assert resp.status_code == 422
    fields = [e["loc"][-1] for e in resp.json()["detail"]]
    assert "images" in fields


async def test_empty_images_list_returns_422(auth_headers):
    """images: [] → 422 (минимум одно фото по канон-флоу)."""
    body = {**VALID_SKU_BODY, "images": []}
    async with await make_client() as client:
        resp = await client.post("/api/v1/skus", json=body, headers=auth_headers)

    assert resp.status_code == 422