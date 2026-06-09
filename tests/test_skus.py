"""
Тесты endpoint POST /api/v1/skus.

DoD-сценарии:
  happy:
    - first_sku_transitions_product_to_on_moderation   [реальный add_sku, мок DB]
    - first_sku_emits_created_event_to_moderation
    - second_sku_no_state_change
    - sku_on_moderated_product_returns_to_on_moderation
    - sku_on_blocked_product_returns_to_on_moderation
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
from app.models.product import Product, ProductStatus, SKU, SKUCharacteristic, SKUImage
from app.services.product_presenter import sku_to_seller_response

# ─── Константы ───────────────────────────────────────────────────────────────

SELLER_ID = uuid.uuid4()
SELLER2_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()
CATEGORY_ID = uuid.uuid4()
_NOW = datetime.now(timezone.utc)

VALID_SKU_BODY = {
    "product_id": str(PRODUCT_ID),
    "name": "Красный M",
    "price": 99900,  # копейки
    "images": [{"url": "https://cdn.example.com/sku1.jpg", "ordering": 0}],
    "characteristics": [{"name": "Размер", "value": "M"}],
}


# ─── Фабрики ─────────────────────────────────────────────────────────────────

def make_product(
        status: ProductStatus = ProductStatus.CREATED,
        seller_id: uuid.UUID = SELLER_ID,
) -> MagicMock:
    p = MagicMock(spec=Product)
    p.id = PRODUCT_ID
    p.seller_id = seller_id
    p.category_id = CATEGORY_ID
    p.title = "Test Product"
    p.status = status
    return p


def make_sku() -> MagicMock:
    s = MagicMock(spec=SKU)
    s.id = uuid.uuid4()
    s.product_id = PRODUCT_ID
    s.name = "Красный M"
    s.price = 99900
    s.discount = 0
    s.cost_price = None
    s.article = None
    s.stock_quantity = 0
    s.reserved_quantity = 0
    s.active_quantity = 0
    s.images_rel = []
    s.characteristics_rel = []
    s.created_at = _NOW
    s.updated_at = _NOW
    return s


def build_real_sku() -> SKU:
    """Реальный ORM-объект SKU с images_rel / characteristics_rel (не MagicMock)."""
    sku_id = uuid.uuid4()
    sku = SKU(
        id=sku_id,
        product_id=PRODUCT_ID,
        name="Красный M",
        price=99900,
        discount=0,
        cost_price=None,
        article=None,
        stock_quantity=0,
        reserved_quantity=0,
        created_at=_NOW,
        updated_at=_NOW,
    )
    sku.images_rel = [
        SKUImage(
            id=uuid.uuid4(),
            sku_id=sku_id,
            url="https://cdn.example.com/sku1.jpg",
            ordering=0,
        )
    ]
    sku.characteristics_rel = [
        SKUCharacteristic(
            id=uuid.uuid4(),
            sku_id=sku_id,
            name="Размер",
            value="M",
        )
    ]
    return sku


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


# ─── Сериализация ORM → SKUResponse ──────────────────────────────────────────

def test_sku_to_seller_response_serializes_real_orm_model():
    """Реальный SKU (images_rel / characteristics_rel) → валидный SKUResponse."""
    sku = build_real_sku()
    response = sku_to_seller_response(sku)

    assert response.id == sku.id
    assert len(response.images) == 1
    assert response.images[0].url == "https://cdn.example.com/sku1.jpg"
    assert response.images[0].ordering == 0
    assert response.images[0].id is not None
    assert len(response.characteristics) == 1
    assert response.characteristics[0].name == "Размер"
    assert response.characteristics[0].value == "M"
    assert response.characteristics[0].id is not None
    assert response.active_quantity == 0


async def test_post_sku_returns_201_with_real_orm_serialization(auth_headers):
    """POST /api/v1/skus: ответ 201 сериализует реальный ORM-объект, не MagicMock."""
    sku = build_real_sku()

    with patch("app.routers.skus.add_sku", new_callable=AsyncMock, return_value=sku):
        async with await make_client() as client:
            resp = await client.post("/api/v1/skus", json=VALID_SKU_BODY, headers=auth_headers)

    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == str(sku.id)
    assert len(data["images"]) == 1
    assert data["images"][0]["url"] == "https://cdn.example.com/sku1.jpg"
    assert "id" in data["images"][0]
    assert len(data["characteristics"]) == 1
    assert data["characteristics"][0]["name"] == "Размер"
    assert data["characteristics"][0]["value"] == "M"
    assert "id" in data["characteristics"][0]


# ─── Happy path ───────────────────────────────────────────────────────────────

async def test_first_sku_transitions_product_to_on_moderation(auth_headers):
    """
    Блокер 4: тест вызывает РЕАЛЬНЫЙ add_sku (без patch на роутер).
    Мокируем только БД и emit_product_created.
    Проверяем, что product.status стал ON_MODERATION.
    """
    product = make_product(ProductStatus.CREATED)
    sku = make_sku()

    db = _db_with_product(product, existing_skus=0)
    # flush() не должен падать; refresh() проставляет поля SKU
    db.refresh.side_effect = lambda obj: None

    app.dependency_overrides[get_db] = lambda: db

    with patch("app.services.sku_service._reload_sku_with_relations", new_callable=AsyncMock, return_value=sku), \
            patch("app.services.sku_service.emit_product_created"):
        async with await make_client() as client:
            resp = await client.post("/api/v1/skus", json=VALID_SKU_BODY, headers=auth_headers)

    assert resp.status_code == 201
    # Ключевой инвариант: статус изменён внутри add_sku
    assert product.status == ProductStatus.ON_MODERATION


async def test_first_sku_emits_created_event_to_moderation(auth_headers):
    """emit_product_created вызывается ровно один раз с корректными полями."""
    product = make_product(ProductStatus.CREATED)
    sku = make_sku()

    db = _db_with_product(product, existing_skus=0)
    db.refresh.side_effect = lambda obj: None
    app.dependency_overrides[get_db] = lambda: db

    with patch("app.services.sku_service._reload_sku_with_relations", new_callable=AsyncMock, return_value=sku), \
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


async def test_first_sku_on_non_created_product_no_moderation(auth_headers):
    """Первый SKU при статусе != CREATED не переводит товар и не шлёт событие."""
    product = make_product(ProductStatus.ON_MODERATION)
    sku = make_sku()

    db = _db_with_product(product, existing_skus=0)
    db.refresh.side_effect = lambda obj: None
    app.dependency_overrides[get_db] = lambda: db

    with patch("app.services.sku_service._reload_sku_with_relations", new_callable=AsyncMock, return_value=sku), \
            patch("app.services.sku_service.emit_product_created") as mock_emit:
        async with await make_client() as client:
            resp = await client.post("/api/v1/skus", json=VALID_SKU_BODY, headers=auth_headers)

    assert resp.status_code == 201
    assert product.status == ProductStatus.ON_MODERATION
    mock_emit.assert_not_called()


async def test_second_sku_no_state_change(auth_headers):
    """Второй SKU не меняет статус и не отправляет событие."""
    product = make_product(ProductStatus.ON_MODERATION)
    sku = make_sku()

    db = _db_with_product(product, existing_skus=1)
    db.refresh.side_effect = lambda obj: None
    app.dependency_overrides[get_db] = lambda: db

    with patch("app.services.sku_service._reload_sku_with_relations", new_callable=AsyncMock, return_value=sku), \
            patch("app.services.sku_service.emit_product_created") as mock_emit:
        async with await make_client() as client:
            resp = await client.post("/api/v1/skus", json=VALID_SKU_BODY, headers=auth_headers)

    assert resp.status_code == 201
    mock_emit.assert_not_called()
    assert product.status == ProductStatus.ON_MODERATION  # без изменений



async def test_sku_on_moderated_product_returns_to_on_moderation(auth_headers):
    """
    Добавление SKU к MODERATED товару → ON_MODERATION + событие EDITED.
    Канон B2B-2: новый непроверенный SKU требует повторной модерации.
    """
    product = make_product(ProductStatus.MODERATED)
    sku = make_sku()

    db = _db_with_product(product, existing_skus=1)
    db.refresh.side_effect = lambda obj: None
    app.dependency_overrides[get_db] = lambda: db

    with patch("app.services.sku_service._reload_sku_with_relations", new_callable=AsyncMock, return_value=sku),             patch("app.services.sku_service.emit_product_created") as mock_created,             patch("app.services.sku_service.emit_product_edited") as mock_edited:
        async with await make_client() as client:
            resp = await client.post("/api/v1/skus", json=VALID_SKU_BODY, headers=auth_headers)

    assert resp.status_code == 201
    assert product.status == ProductStatus.ON_MODERATION
    mock_created.assert_not_called()
    mock_edited.assert_called_once_with(
        product_id=product.id,
        seller_id=product.seller_id,
        category_id=product.category_id,
        title=product.title,
        sku_id=sku.id,
        price=sku.price,
    )


async def test_sku_on_blocked_product_returns_to_on_moderation(auth_headers):
    """
    Добавление SKU к BLOCKED товару → ON_MODERATION + событие EDITED.
    """
    product = make_product(ProductStatus.BLOCKED)
    sku = make_sku()

    db = _db_with_product(product, existing_skus=1)
    db.refresh.side_effect = lambda obj: None
    app.dependency_overrides[get_db] = lambda: db

    with patch("app.services.sku_service._reload_sku_with_relations", new_callable=AsyncMock, return_value=sku),             patch("app.services.sku_service.emit_product_created") as mock_created,             patch("app.services.sku_service.emit_product_edited") as mock_edited:
        async with await make_client() as client:
            resp = await client.post("/api/v1/skus", json=VALID_SKU_BODY, headers=auth_headers)

    assert resp.status_code == 201
    assert product.status == ProductStatus.ON_MODERATION
    mock_created.assert_not_called()
    mock_edited.assert_called_once()


# ─── Unhappy path ─────────────────────────────────────────────────────────────

async def test_add_sku_to_hard_blocked_returns_403(auth_headers):
    """HARD_BLOCKED товар → 403."""
    product = make_product(ProductStatus.HARD_BLOCKED)
    db = _db_with_product(product)
    app.dependency_overrides[get_db] = lambda: db

    async with await make_client() as client:
        resp = await client.post("/api/v1/skus", json=VALID_SKU_BODY, headers=auth_headers)

    assert resp.status_code == 403
    body = resp.json()
    assert {"code", "message"} <= set(body.keys())
    assert body["code"] in ("ERROR", "FORBIDDEN") or "HARD_BLOCKED" in body["message"]


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
    body = resp.json()
    assert {"code", "message"} <= set(body.keys())
    assert body["code"] in ("NOT_FOUND", "ERROR") or "not found" in body["message"].lower()


async def test_missing_name_returns_422(auth_headers):
    """Блокер 2: тело без обязательного name → 422."""
    body = {k: v for k, v in VALID_SKU_BODY.items() if k != "name"}
    async with await make_client() as client:
        resp = await client.post("/api/v1/skus", json=body, headers=auth_headers)

    assert resp.status_code == 422
    body = resp.json()
    assert {"code", "message"} <= set(body.keys())
    assert body["code"] == "VALIDATION_ERROR"


async def test_missing_images_field_returns_422(auth_headers):
    """Тело без поля images → 422 (images default=[] — поле не обязательно,
    но тест на пустой список images оставлен как документация канон-флоу)."""
    body = {**VALID_SKU_BODY, "images": []}
    # images не обязательны по спецификации (default: []), 201 ожидается
    product = make_product(ProductStatus.CREATED)
    sku = make_sku()
    db = _db_with_product(product, existing_skus=0)
    db.refresh.side_effect = lambda obj: None
    app.dependency_overrides[get_db] = lambda: db

    with patch("app.services.sku_service._reload_sku_with_relations", new_callable=AsyncMock, return_value=sku), \
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
    body = resp.json()
    assert {"code", "message"} <= set(body.keys())
    assert body["code"] == "VALIDATION_ERROR"


# ─── DELETE /api/v1/skus/{sku_id} ─────────────────────────────────────────────

SKU_ID = uuid.uuid4()


def _make_sku_for_delete(
    product_status: ProductStatus = ProductStatus.MODERATED,
    reserved_quantity: int = 0,
    active_quantity: int = 0,
    seller_id: uuid.UUID = SELLER_ID,
) -> tuple[MagicMock, MagicMock]:
    product = MagicMock(spec=Product)
    product.id = PRODUCT_ID
    product.seller_id = seller_id
    product.status = product_status
    product.title = "Test Product"
    product.category_id = CATEGORY_ID

    sku = MagicMock(spec=SKU)
    sku.id = SKU_ID
    sku.product_id = PRODUCT_ID
    sku.product = product
    sku.reserved_quantity = reserved_quantity
    sku.active_quantity = active_quantity
    sku.name = "Test SKU"
    sku.price = 99900

    return sku, product


def _db_with_sku_and_count(sku: MagicMock, remaining_count: int = 0) -> AsyncMock:
    db = AsyncMock()
    first_result = MagicMock()
    first_result.scalar_one_or_none.return_value = sku
    second_result = MagicMock()
    second_result.scalar_one.return_value = remaining_count
    db.execute = AsyncMock(side_effect=[first_result, second_result])
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    return db


async def test_delete_sku_succeeds(auth_headers):
    """Happy path: SKU удалён, 204, без side-эффектов."""
    sku, product = _make_sku_for_delete(
        product_status=ProductStatus.MODERATED,
        reserved_quantity=0,
        active_quantity=0,
    )
    # Есть ещё SKU → not last
    db = _db_with_sku_and_count(sku, remaining_count=1)
    app.dependency_overrides[get_db] = lambda: db

    with (
        patch("app.services.sku_service.emit_product_deleted") as mock_deleted,
        patch("app.services.sku_service.emit_sku_out_of_stock") as mock_oos,
    ):
        async with await make_client() as client:
            resp = await client.delete(f"/api/v1/skus/{SKU_ID}", headers=auth_headers)

    assert resp.status_code == 204
    db.delete.assert_called_once_with(sku)
    mock_deleted.assert_not_called()
    mock_oos.assert_not_called()


async def test_delete_sku_with_active_reserves_returns_409(auth_headers):
    """reserved_quantity > 0 → 409 CONFLICT, SKU не удалён."""
    sku, product = _make_sku_for_delete(reserved_quantity=5)
    db = _db_with_sku_and_count(sku)
    app.dependency_overrides[get_db] = lambda: db

    with (
        patch("app.services.sku_service.emit_product_deleted") as mock_deleted,
        patch("app.services.sku_service.emit_sku_out_of_stock") as mock_oos,
    ):
        async with await make_client() as client:
            resp = await client.delete(f"/api/v1/skus/{SKU_ID}", headers=auth_headers)

    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "CONFLICT"
    assert "reserves" in body["message"].lower()
    db.delete.assert_not_called()
    mock_deleted.assert_not_called()
    mock_oos.assert_not_called()


async def test_last_sku_on_moderation_transitions_product_to_created(auth_headers):
    """
    Последний SKU удалён + товар ON_MODERATION → товар CREATED + событие DELETED.
    """
    sku, product = _make_sku_for_delete(product_status=ProductStatus.ON_MODERATION)
    # Это последний SKU
    db = _db_with_sku_and_count(sku, remaining_count=0)
    app.dependency_overrides[get_db] = lambda: db

    with (
        patch("app.services.sku_service.emit_product_deleted") as mock_deleted,
        patch("app.services.sku_service.emit_sku_out_of_stock") as mock_oos,
    ):
        async with await make_client() as client:
            resp = await client.delete(f"/api/v1/skus/{SKU_ID}", headers=auth_headers)

    assert resp.status_code == 204
    assert product.status == ProductStatus.CREATED
    db.delete.assert_called_once_with(sku)
    mock_deleted.assert_called_once_with(
        product_id=product.id,
        seller_id=product.seller_id,
        category_id=product.category_id,
        title=product.title,
    )
    mock_oos.assert_not_called()


async def test_delete_sku_hard_blocked_product_returns_403(auth_headers):
    """Товар HARD_BLOCKED → 403 FORBIDDEN, SKU не удалён."""
    sku, product = _make_sku_for_delete(product_status=ProductStatus.HARD_BLOCKED)
    db = _db_with_sku_and_count(sku)
    # db.execute может быть вызван только один раз (первая проверка выбрасывает исключение)
    # но side_effect с 2 элементами не сломается, т.к. второй execute не дойдёт
    app.dependency_overrides[get_db] = lambda: db

    with (
        patch("app.services.sku_service.emit_product_deleted") as mock_deleted,
        patch("app.services.sku_service.emit_sku_out_of_stock") as mock_oos,
    ):
        async with await make_client() as client:
            resp = await client.delete(f"/api/v1/skus/{SKU_ID}", headers=auth_headers)

    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "FORBIDDEN"
    assert "hard-blocked" in body["message"].lower()
    db.delete.assert_not_called()
    mock_deleted.assert_not_called()
    mock_oos.assert_not_called()


async def test_sku_out_of_stock_event_on_moderated_product(auth_headers):
    """
    active_quantity > 0 + товар MODERATED → событие SKU_OUT_OF_STOCK в B2C.
    Товар не теряет последний SKU (remaining_count=1), статус не меняется.
    """
    sku, product = _make_sku_for_delete(
        product_status=ProductStatus.MODERATED,
        active_quantity=5,
    )
    # Есть ещё SKU → not last
    db = _db_with_sku_and_count(sku, remaining_count=1)
    app.dependency_overrides[get_db] = lambda: db

    with (
        patch("app.services.sku_service.emit_product_deleted") as mock_deleted,
        patch("app.services.sku_service.emit_sku_out_of_stock") as mock_oos,
    ):
        async with await make_client() as client:
            resp = await client.delete(f"/api/v1/skus/{SKU_ID}", headers=auth_headers)

    assert resp.status_code == 204
    assert product.status == ProductStatus.MODERATED  # не изменился
    db.delete.assert_called_once_with(sku)
    mock_deleted.assert_not_called()
    mock_oos.assert_called_once_with(sku.id, sku.product_id, 0)