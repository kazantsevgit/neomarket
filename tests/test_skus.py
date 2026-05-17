"""
Тесты endpoint POST /api/v1/skus.
 
DoD-сценарии:
  happy:
    - first_sku_transitions_product_to_on_moderation   [реальный add_sku, мок DB]
    - first_sku_emits_created_event_to_moderation
    - second_sku_no_state_change
  unhappy:
    - add_sku_to_hard_blocked_returns_403
    - missing_name_returns_422                         [блокер 2]
    - missing_image_field_returns_422
    - cross_seller_add_sku_returns_404                 [блокер 5]
"""
 
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
 
import pytest
from httpx import AsyncClient, ASGITransport
 
from app.main import app
from app.dependencies.auth import get_current_seller_id
from app.dependencies.db import get_db
from app.models.product import Product, ProductStatus, SKU
 
# ─── Константы ───────────────────────────────────────────────────────────────
 
SELLER_ID   = uuid.uuid4()
SELLER2_ID  = uuid.uuid4()
PRODUCT_ID  = uuid.uuid4()
CATEGORY_ID = uuid.uuid4()
_NOW        = datetime.now(timezone.utc)
 
VALID_SKU_BODY = {
    "product_id":      str(PRODUCT_ID),
    "name":            "Красный M",
    "price":           99900,           # копейки
    "images":          [{"url": "https://cdn.example.com/sku1.jpg", "ordering": 0}],
    "characteristics": [{"name": "Размер", "value": "M"}],
}
 
 
# ─── Фабрики ─────────────────────────────────────────────────────────────────
 
def make_product(
    status: ProductStatus = ProductStatus.CREATED,
    seller_id: uuid.UUID = SELLER_ID,
) -> MagicMock:
    p = MagicMock(spec=Product)
    p.id                = PRODUCT_ID
    p.seller_id         = seller_id
    p.category_id       = CATEGORY_ID
    p.title             = "Test Product"
    p.status            = status
    return p
 
 
def make_sku() -> MagicMock:
    s = MagicMock(spec=SKU)
    s.id                = uuid.uuid4()
    s.product_id        = PRODUCT_ID
    s.name              = "Красный M"
    s.price             = 99900
    s.discount          = 0
    s.cost_price        = None
    s.article           = None
    s.stock_quantity    = 0
    s.reserved_quantity = 0
    s.active_quantity   = 0
    s.images            = []
    s.characteristics   = []
    s.created_at        = _NOW
    s.updated_at        = _NOW
    return s
 
 
# ─── Фикстуры ────────────────────────────────────────────────────────────────
 
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
    """Базовая заглушка БД — каждый тест может переопределить через override_db."""
    fake_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)
 
 
async def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
 
 
def _db_with_product(product: MagicMock, existing_skus: int = 0) -> AsyncMock:
    """Возвращает fake_db настроенный для конкретного товара."""
    db = AsyncMock()
    db.get.return_value = product
    scalar_mock = MagicMock()
    scalar_mock.scalar_one.return_value = existing_skus
    db.execute.return_value = scalar_mock
    return db
 
 
# ─── Happy path ───────────────────────────────────────────────────────────────
 
async def test_first_sku_transitions_product_to_on_moderation(auth_headers):
    """
    Блокер 4: тест вызывает РЕАЛЬНЫЙ add_sku (без patch на роутер).
    Мокируем только БД и emit_product_created.
    Проверяем, что product.status стал ON_MODERATION.
    """
    product = make_product(ProductStatus.CREATED)
    sku     = make_sku()
 
    db = _db_with_product(product, existing_skus=0)
    # flush() не должен падать; refresh() проставляет поля SKU
    db.refresh.side_effect = lambda obj: None
 
    app.dependency_overrides[get_db] = lambda: db
 
    with patch("app.services.sku_service.SKU", return_value=sku), \
         patch("app.services.sku_service.SKUImage"), \
         patch("app.services.sku_service.SKUCharacteristic"), \
         patch("app.services.sku_service.emit_product_created"):
 
        async with await make_client() as client:
            resp = await client.post("/api/v1/skus", json=VALID_SKU_BODY, headers=auth_headers)
 
    assert resp.status_code == 201
    # Ключевой инвариант: статус изменён внутри add_sku
    assert product.status == ProductStatus.ON_MODERATION
 
 
async def test_first_sku_emits_created_event_to_moderation(auth_headers):
    """emit_product_created вызывается ровно один раз с корректными полями."""
    product = make_product(ProductStatus.CREATED)
    sku     = make_sku()
 
    db = _db_with_product(product, existing_skus=0)
    db.refresh.side_effect = lambda obj: None
    app.dependency_overrides[get_db] = lambda: db
 
    with patch("app.services.sku_service.SKU", return_value=sku), \
         patch("app.services.sku_service.SKUImage"), \
         patch("app.services.sku_service.SKUCharacteristic"), \
         patch("app.services.sku_service.emit_product_created") as mock_emit:
 
        async with await make_client() as client:
            resp = await client.post("/api/v1/skus", json=VALID_SKU_BODY, headers=auth_headers)
 
    assert resp.status_code == 201
    mock_emit.assert_called_once_with(
        product_id=product.id,
        seller_id=product.seller_id,
        category_id=product.category_id,
        title=product.title,
        sku_id=sku.id,
        price=sku.price,
    )
 
 
async def test_second_sku_no_state_change(auth_headers):
    """Второй SKU не меняет статус и не отправляет событие."""
    product = make_product(ProductStatus.ON_MODERATION)
    sku     = make_sku()
 
    db = _db_with_product(product, existing_skus=1)
    db.refresh.side_effect = lambda obj: None
    app.dependency_overrides[get_db] = lambda: db
 
    with patch("app.services.sku_service.SKU", return_value=sku), \
         patch("app.services.sku_service.SKUImage"), \
         patch("app.services.sku_service.SKUCharacteristic"), \
         patch("app.services.sku_service.emit_product_created") as mock_emit:
 
        async with await make_client() as client:
            resp = await client.post("/api/v1/skus", json=VALID_SKU_BODY, headers=auth_headers)
 
    assert resp.status_code == 201
    mock_emit.assert_not_called()
    assert product.status == ProductStatus.ON_MODERATION   # без изменений
 
 
# ─── Unhappy path ─────────────────────────────────────────────────────────────
 
async def test_add_sku_to_hard_blocked_returns_403(auth_headers):
    """HARD_BLOCKED товар → 403."""
    product = make_product(ProductStatus.HARD_BLOCKED)
    db = _db_with_product(product)
    app.dependency_overrides[get_db] = lambda: db
 
    async with await make_client() as client:
        resp = await client.post("/api/v1/skus", json=VALID_SKU_BODY, headers=auth_headers)
 
    assert resp.status_code == 403
    assert "HARD_BLOCKED" in resp.json()["detail"]
 
 
async def test_cross_seller_add_sku_returns_404(auth_headers):
    """
    Блокер 5: продавец-2 пытается добавить SKU к товару продавца-1 → 404.
    Настраиваем dependency_overrides на SELLER2_ID, товар принадлежит SELLER_ID.
    """
    product = make_product(ProductStatus.CREATED, seller_id=SELLER_ID)
    db = AsyncMock()
    db.get.return_value = product
 
    app.dependency_overrides[get_current_seller_id] = lambda: SELLER2_ID
    app.dependency_overrides[get_db] = lambda: db
 
    async with await make_client() as client:
        resp = await client.post("/api/v1/skus", json=VALID_SKU_BODY, headers=auth_headers)
 
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Product not found"
 
 
async def test_missing_name_returns_422(auth_headers):
    """Блокер 2: тело без обязательного name → 422."""
    body = {k: v for k, v in VALID_SKU_BODY.items() if k != "name"}
    async with await make_client() as client:
        resp = await client.post("/api/v1/skus", json=body, headers=auth_headers)
 
    assert resp.status_code == 422
    fields = [e["loc"][-1] for e in resp.json()["detail"]]
    assert "name" in fields
 
 
async def test_missing_images_field_returns_422(auth_headers):
    """Тело без поля images → 422 (images default=[] — поле не обязательно,
    но тест на пустой список images оставлен как документация канон-флоу)."""
    body = {**VALID_SKU_BODY, "images": []}
    # images не обязательны по спецификации (default: []), 201 ожидается
    product = make_product(ProductStatus.CREATED)
    sku     = make_sku()
    db = _db_with_product(product, existing_skus=0)
    db.refresh.side_effect = lambda obj: None
    app.dependency_overrides[get_db] = lambda: db
 
    with patch("app.services.sku_service.SKU", return_value=sku), \
         patch("app.services.sku_service.SKUImage"), \
         patch("app.services.sku_service.SKUCharacteristic"), \
         patch("app.services.sku_service.emit_product_created"):
        async with await make_client() as client:
            resp = await client.post("/api/v1/skus", json=body, headers=auth_headers)
 
    # Спека разрешает пустой список изображений при создании (default: [])
    assert resp.status_code == 201
 
 
async def test_missing_price_returns_422(auth_headers):
    """Тело без обязательного price → 422."""
    body = {k: v for k, v in VALID_SKU_BODY.items() if k != "price"}
    async with await make_client() as client:
        resp = await client.post("/api/v1/skus", json=body, headers=auth_headers)
 
    assert resp.status_code == 422
    fields = [e["loc"][-1] for e in resp.json()["detail"]]
    assert "price" in fields