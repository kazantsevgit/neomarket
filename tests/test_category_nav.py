"""
US-CAT-05: навигация по категориям.

DoD-сценарии:
  happy:
    - category_tree_returns_nested_structure
    - breadcrumbs_return_path_from_root
  unhappy:
    - unknown_category_returns_404
    - orphan_node_returns_422
    - ambiguous_params_returns_400
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.dependencies.db import get_db
from app.models.category import Category
from app.models.product import Product, ProductStatus

# ─── Фабрики ─────────────────────────────────────────────────────────────────

ROOT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
CHILD_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
LEAF_ID  = uuid.UUID("00000000-0000-0000-0000-000000000003")
ORPHAN_PARENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")  # не существует


def make_category(
    cat_id: uuid.UUID,
    name: str,
    parent_id: uuid.UUID | None = None,
    slug: str | None = None,
) -> MagicMock:
    c = MagicMock(spec=Category)
    c.id = cat_id
    c.name = name
    c.parent_id = parent_id
    c.slug = slug
    c.description = None
    c.image_url = None
    c.is_active = True
    c.seo = None
    c.meta_tags = None
    c.created_at = None
    c.updated_at = None
    return c


def make_product(
    product_id: uuid.UUID,
    category_id: uuid.UUID,
    status: ProductStatus = ProductStatus.MODERATED,
    deleted: bool = False,
) -> MagicMock:
    p = MagicMock(spec=Product)
    p.id = product_id
    p.category_id = category_id
    p.status = status
    p.deleted = deleted
    return p


# ─── Фикстура БД ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def override_db():
    fake_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _make_execute_result(rows: list) -> MagicMock:
    """Имитирует результат db.execute() → .scalars().all()."""
    result = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = rows
    result.scalars.return_value = scalars_mock
    return result


# ─── happy: дерево категорий ──────────────────────────────────────────────────

async def test_category_tree_returns_nested_structure(override_db):
    """
    happy: дерево собирается из плоского списка.
    Электроника → Смартфоны → Android.
    """
    root  = make_category(ROOT_ID,  "Электроника", parent_id=None,    slug="electronics")
    child = make_category(CHILD_ID, "Смартфоны",   parent_id=ROOT_ID, slug="smartphones")
    leaf  = make_category(LEAF_ID,  "Android",     parent_id=CHILD_ID, slug="android")

    override_db.execute.return_value = _make_execute_result([root, child, leaf])

    async with await _client() as client:
        resp = await client.get("/api/v1/categories")

    assert resp.status_code == 200
    data = resp.json()
    items = data["items"]

    # один корневой узел
    assert len(items) == 1
    root_node = items[0]
    assert root_node["id"] == str(ROOT_ID)
    assert root_node["name"] == "Электроника"
    assert root_node["parent_id"] is None

    # один дочерний
    assert len(root_node["children"]) == 1
    child_node = root_node["children"][0]
    assert child_node["id"] == str(CHILD_ID)
    assert child_node["parent_id"] == str(ROOT_ID)

    # лист
    assert len(child_node["children"]) == 1
    leaf_node = child_node["children"][0]
    assert leaf_node["id"] == str(LEAF_ID)
    assert leaf_node["parent_id"] == str(CHILD_ID)
    assert leaf_node["children"] == []


# ─── happy: хлебные крошки ────────────────────────────────────────────────────

async def test_breadcrumbs_return_path_from_root(override_db):
    """
    happy: breadcrumbs для LEAF_ID возвращают цепочку Электроника → Смартфоны → Android.
    """
    root  = make_category(ROOT_ID,  "Электроника", parent_id=None,     slug="electronics")
    child = make_category(CHILD_ID, "Смартфоны",   parent_id=ROOT_ID,  slug="smartphones")
    leaf  = make_category(LEAF_ID,  "Android",     parent_id=CHILD_ID, slug="android")

    override_db.execute.return_value = _make_execute_result([root, child, leaf])

    async with await _client() as client:
        resp = await client.get("/api/v1/breadcrumbs", params={"category_id": str(LEAF_ID)})

    assert resp.status_code == 200
    data = resp.json()

    crumbs = data["data"]
    assert len(crumbs) == 3

    assert crumbs[0]["id"] == str(ROOT_ID)
    assert crumbs[0]["level"] == 0
    assert crumbs[0]["is_current"] is False
    assert crumbs[0]["slug"] == "electronics"

    assert crumbs[1]["id"] == str(CHILD_ID)
    assert crumbs[1]["level"] == 1
    assert crumbs[1]["is_current"] is False

    assert crumbs[2]["id"] == str(LEAF_ID)
    assert crumbs[2]["level"] == 2
    assert crumbs[2]["is_current"] is True

    # meta
    meta = data["meta"]
    assert meta["resolved_via"] == "category_id"
    assert meta["category_id"] == str(LEAF_ID)


# ─── unhappy: несуществующая категория ───────────────────────────────────────

async def test_unknown_category_returns_404(override_db):
    """
    unhappy: GET /categories/{unknown_id} → 404 NOT_FOUND.
    """
    unknown_id = uuid.uuid4()
    override_db.get.return_value = None

    async with await _client() as client:
        resp = await client.get(f"/api/v1/categories/{unknown_id}")

    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


# ─── unhappy: orphan node ────────────────────────────────────────────────────

async def test_orphan_node_returns_422(override_db):
    """
    unhappy: категория ссылается на parent_id, которого нет в списке → 422 orphan_node.
    """
    orphan = make_category(CHILD_ID, "Сирота", parent_id=ORPHAN_PARENT_ID, slug="orphan")

    override_db.execute.return_value = _make_execute_result([orphan])

    async with await _client() as client:
        resp = await client.get("/api/v1/categories")

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "orphan_node"


# ─── unhappy: ambiguous params ───────────────────────────────────────────────

async def test_ambiguous_params_returns_400(override_db):
    """
    unhappy: одновременно переданы category_id и product_id → 400 ambiguous_param.
    """
    async with await _client() as client:
        resp = await client.get(
            "/api/v1/breadcrumbs",
            params={
                "category_id": str(uuid.uuid4()),
                "product_id": str(uuid.uuid4()),
            },
        )

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "ambiguous_param"


# ─── дополнительно: breadcrumbs без параметров ───────────────────────────────

async def test_missing_param_returns_400(override_db):
    """
    unhappy: ни category_id, ни product_id → 400 missing_param.
    """
    async with await _client() as client:
        resp = await client.get("/api/v1/breadcrumbs")

    assert resp.status_code == 400
    assert resp.json()["error"] == "missing_param"


# ─── дополнительно: breadcrumbs для несуществующей категории ─────────────────

async def test_breadcrumbs_unknown_category_returns_404(override_db):
    """
    unhappy: category_id не существует в БД → 404.
    """
    override_db.execute.return_value = _make_execute_result([])  # пустой список

    async with await _client() as client:
        resp = await client.get(
            "/api/v1/breadcrumbs",
            params={"category_id": str(uuid.uuid4())},
        )

    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"