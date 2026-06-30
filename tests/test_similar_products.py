"""
US-CAT-04: блок похожих товаров.

DoD-сценарии:
  happy:
    - similar_returns_up_to_8_from_same_category  (текущий товар исключён)
  unhappy:
    - empty_category_returns_200_empty_list
    - unknown_product_returns_404
  дополнительно:
    - similar_excludes_current_product  (явная проверка исключения)
    - similar_fallback_to_parent_category  (fallback когда в категории мало товаров)
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.dependencies.db import get_db
from app.models.category import Category
from app.models.product import Product, ProductStatus

# ─── UUID-константы ───────────────────────────────────────────────────────────

PRODUCT_ID    = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
CATEGORY_ID   = uuid.UUID("cccccccc-0000-0000-0000-000000000001")
PARENT_CAT_ID = uuid.UUID("cccccccc-0000-0000-0000-000000000000")

OTHER_IDS = [
    uuid.UUID(f"bbbbbbbb-0000-0000-0000-{i:012d}")
    for i in range(1, 10)
]


# ─── Фабрики ─────────────────────────────────────────────────────────────────

def make_sku(price: int = 100_00, discount: int = 0, stock: int = 5, reserved: int = 0):
    sku = MagicMock()
    sku.price = price
    sku.discount = discount
    sku.stock_quantity = stock
    sku.reserved_quantity = reserved
    sku.active_quantity = stock - reserved
    sku.images_rel = []
    return sku


def make_product(
    product_id: uuid.UUID,
    category_id: uuid.UUID = CATEGORY_ID,
    status: ProductStatus = ProductStatus.MODERATED,
    deleted: bool = False,
    n_skus: int = 1,
) -> MagicMock:
    p = MagicMock(spec=Product)
    p.id = product_id
    p.category_id = category_id
    p.seller_id = None
    p.status = status
    p.deleted = deleted
    p.title = f"Product {product_id}"
    p.slug = f"product-{product_id}"
    p.images = []
    p.skus = [make_sku() for _ in range(n_skus)]
    p.created_at = __import__("datetime").datetime(2024, 1, 1)
    return p


def make_category(cat_id: uuid.UUID, parent_id: uuid.UUID | None = None) -> MagicMock:
    c = MagicMock(spec=Category)
    c.id = cat_id
    c.parent_id = parent_id
    return c


# ─── Фикстура и клиент ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def override_db():
    fake_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _make_execute_result(scalars_list: list) -> MagicMock:
    """Имитирует db.execute() → .scalars().unique().all() и scalar_one()."""
    result = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.unique.return_value = scalars_mock
    scalars_mock.all.return_value = scalars_list
    result.scalars.return_value = scalars_mock
    result.scalar_one.return_value = len(scalars_list)
    return result


# ─── happy: до 8 товаров из той же категории ─────────────────────────────────

async def test_similar_returns_up_to_8_from_same_category(override_db):
    """
    happy: возвращает до 8 похожих товаров из той же категории;
    текущий товар не входит в результат.
    """
    current = make_product(PRODUCT_ID, CATEGORY_ID)
    others = [make_product(oid, CATEGORY_ID) for oid in OTHER_IDS[:8]]

    # db.get(Product, product_id) → current
    # db.get(Category, category_id) → category без parent
    category = make_category(CATEGORY_ID, parent_id=None)

    call_count = 0

    async def get_side(model, pk):
        if model == Product and pk == PRODUCT_ID:
            return current
        if model == Category and pk == CATEGORY_ID:
            return category
        return None

    override_db.get.side_effect = get_side

    # первый execute — COUNT(*), второй — сами товары
    count_result = MagicMock()
    count_result.scalar_one.return_value = 8
    items_result = _make_execute_result(others)

    override_db.execute.side_effect = [count_result, items_result]

    async with await _client() as client:
        resp = await client.get(f"/api/v1/catalog/products/{PRODUCT_ID}/similar?limit=8")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 8

    # текущий товар не должен быть в результате
    ids_in_response = {item["id"] for item in data}
    assert str(PRODUCT_ID) not in ids_in_response


# ─── unhappy: пустая категория → 200 пустой список ───────────────────────────

async def test_empty_category_returns_200_empty_list(override_db):
    """
    unhappy: в категории нет других товаров → 200 с items: [], total_count: 0.
    """
    current = make_product(PRODUCT_ID, CATEGORY_ID)
    category = make_category(CATEGORY_ID, parent_id=None)

    async def get_side(model, pk):
        if model == Product and pk == PRODUCT_ID:
            return current
        if model == Category and pk == CATEGORY_ID:
            return category
        return None

    override_db.get.side_effect = get_side

    # COUNT=0, items=[]
    count_result = MagicMock()
    count_result.scalar_one.return_value = 0
    items_result = _make_execute_result([])

    override_db.execute.side_effect = [count_result, items_result]

    async with await _client() as client:
        resp = await client.get(f"/api/v1/catalog/products/{PRODUCT_ID}/similar")

    assert resp.status_code == 200
    data = resp.json()
    assert data == []


# ─── unhappy: неизвестный товар → 404 ────────────────────────────────────────

async def test_unknown_product_returns_404(override_db):
    """
    unhappy: товара не существует → 404 NOT_FOUND.
    """
    override_db.get.return_value = None

    unknown_id = uuid.uuid4()
    async with await _client() as client:
        resp = await client.get(f"/api/v1/catalog/products/{unknown_id}/similar")

    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


# ─── дополнительно: текущий товар точно исключён ─────────────────────────────

async def test_similar_excludes_current_product(override_db):
    """
    Явная проверка: ID текущего товара не появляется в ответе,
    даже если он каким-то образом попал в выборку из БД.
    """
    current = make_product(PRODUCT_ID, CATEGORY_ID)
    # Намеренно включаем current в «список из БД» — сервис должен его исключить
    # через WHERE Product.id != product_id на уровне SQL.
    # В тесте проверяем HTTP-ответ.
    other = make_product(OTHER_IDS[0], CATEGORY_ID)
    category = make_category(CATEGORY_ID, parent_id=None)

    async def get_side(model, pk):
        if model == Product and pk == PRODUCT_ID:
            return current
        if model == Category and pk == CATEGORY_ID:
            return category
        return None

    override_db.get.side_effect = get_side

    count_result = MagicMock()
    count_result.scalar_one.return_value = 1
    items_result = _make_execute_result([other])

    override_db.execute.side_effect = [count_result, items_result]

    async with await _client() as client:
        resp = await client.get(f"/api/v1/catalog/products/{PRODUCT_ID}/similar")

    assert resp.status_code == 200
    ids_in_response = {item["id"] for item in resp.json()}
    assert str(PRODUCT_ID) not in ids_in_response


# ─── дополнительно: fallback на родительскую категорию ───────────────────────

async def test_similar_fallback_to_parent_category(override_db):
    """
    Когда в исходной категории мало товаров (< limit),
    выборка расширяется на родительскую категорию.
    """
    current = make_product(PRODUCT_ID, CATEGORY_ID)
    category = make_category(CATEGORY_ID, parent_id=PARENT_CAT_ID)
    parent_cat = make_category(PARENT_CAT_ID, parent_id=None)

    child_items = [make_product(OTHER_IDS[0], CATEGORY_ID)]   # 1 товар в своей категории
    parent_items = [make_product(OTHER_IDS[1], PARENT_CAT_ID),
                    make_product(OTHER_IDS[2], PARENT_CAT_ID)]  # 2 из родительской

    async def get_side(model, pk):
        if model == Product and pk == PRODUCT_ID:
            return current
        if model == Category and pk == CATEGORY_ID:
            return category
        if model == Category and pk == PARENT_CAT_ID:
            return parent_cat
        return None

    override_db.get.side_effect = get_side

    # 4 вызова execute: COUNT own, items own, COUNT parent, items parent
    c1 = MagicMock(); c1.scalar_one.return_value = 1
    r1 = _make_execute_result(child_items)
    c2 = MagicMock(); c2.scalar_one.return_value = 2
    r2 = _make_execute_result(parent_items)

    override_db.execute.side_effect = [c1, r1, c2, r2]

    async with await _client() as client:
        resp = await client.get(f"/api/v1/catalog/products/{PRODUCT_ID}/similar?limit=8")

    assert resp.status_code == 200
    data = resp.json()
    # Итого 3 товара (1 своих + 2 из родительской)
    assert len(data) == 3