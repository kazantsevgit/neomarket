"""
B2C-2: текстовый поиск товаров (US-CAT-02).

Канон: flows/b2c-catalog-flows.md#b2c-2-search
- GET /api/v1/products?search=... (B2B-7, X-Service-Key) — используется B2C.
- GET /api/v1/catalog/products?q=... — публичный B2C-проксирующий эндпоинт.

Поиск выполняется через SQL ILIKE по title и description (с условием видимости
MODERATED + не удалён + есть SKU в наличии), спецсимволы LIKE (%, _) экранируются.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.dependencies.db import get_db
from app.main import app
from app.models.product import Product, ProductStatus
from app.schemas.catalog import ProductShortListResponse
from app.services import catalog_service

CATEGORY_ID = uuid.uuid4()
PRODUCT_ID_1 = uuid.uuid4()
PRODUCT_ID_2 = uuid.uuid4()
_NOW = datetime.now(timezone.utc)

SERVICE_HEADERS = {"X-Service-Key": settings.B2B_SERVICE_KEY}


def make_product(
    *,
    product_id: uuid.UUID = PRODUCT_ID_1,
    title: str = "iPhone 15 Pro Max",
    description: str = "Флагманский смартфон Apple",
    with_stock: bool = True,
) -> MagicMock:
    p = MagicMock(spec=Product)
    p.id = product_id
    p.title = title
    p.description = description
    p.category_id = CATEGORY_ID
    p.status = ProductStatus.MODERATED
    p.deleted = False
    p.images = [{"id": str(uuid.uuid4()), "url": "https://cdn.neomarket.ru/images/iphone15.jpg", "ordering": 0}]
    p.characteristics = [{"name": "Бренд", "value": "Apple"}]
    sku = MagicMock()
    sku.stock_quantity = 10 if with_stock else 0
    sku.reserved_quantity = 0
    p.skus = [sku]
    p.created_at = _NOW
    return p


@pytest.fixture(autouse=True)
def mock_db():
    fake_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    yield fake_db
    app.dependency_overrides.pop(get_db, None)


async def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── Happy path ────────────────────────────────────────────────────────────────


async def test_search_returns_matching_products(mock_db):
    """Поиск находит товары по title и description (B2C-2)."""
    products = [
        (make_product(product_id=PRODUCT_ID_1, title="iPhone 15 Pro Max"), 12_999_000),
        (make_product(
            product_id=PRODUCT_ID_2,
            title="Чехол для телефона",
            description="Подходит для iPhone 15",
        ), 99_000),
    ]

    count_result = MagicMock()
    count_result.scalar_one.return_value = 2
    list_result = MagicMock()
    list_result.all.return_value = products
    mock_db.execute = AsyncMock(side_effect=[count_result, list_result])

    result = await catalog_service.list_catalog_products(
        mock_db,
        search="iPhone",
        limit=20,
        offset=0,
    )

    assert result.total_count == 2
    assert {item.name for item in result.items} == {"iPhone 15 Pro Max", "Чехол для телефона"}


async def test_search_proxied_through_catalog_endpoint(mock_db):
    """B2C-эндпоинт /api/v1/catalog/products?q=... проксирует поиск в B2B."""
    payload = ProductShortListResponse(items=[], total_count=0, limit=20, offset=0)

    async with await make_client() as client:
        with patch(
            "app.services.b2b_client.list_products",
            new_callable=AsyncMock,
            return_value=payload,
        ) as mocked:
            resp = await client.get("/api/v1/catalog/products", params={"q": "наушники"})

    assert resp.status_code == 200
    mocked.assert_awaited_once()
    assert mocked.await_args.kwargs["search"] == "наушники"


# ── Edge cases ────────────────────────────────────────────────────────────────


async def test_short_query_returns_400(mock_db):
    """Запрос короче 3 символов -> 400 INVALID_REQUEST."""
    async with await make_client() as client:
        resp = await client.get("/api/v1/catalog/products", params={"q": "ip"})

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "INVALID_REQUEST"
    assert "at least 3 characters" in body["message"]


async def test_long_query_returns_400(mock_db):
    """Запрос длиннее 255 символов -> 400 INVALID_REQUEST."""
    async with await make_client() as client:
        resp = await client.get("/api/v1/catalog/products", params={"q": "a" * 256})

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "INVALID_REQUEST"
    assert "at most 255 characters" in body["message"]


async def test_special_chars_do_not_break_query(mock_db):
    """Спецсимволы (%, _, ') не ломают запрос и не трактуются как LIKE-wildcard'ы."""
    count_result = MagicMock()
    count_result.scalar_one.return_value = 0
    list_result = MagicMock()
    list_result.all.return_value = []
    mock_db.execute = AsyncMock(side_effect=[count_result, list_result])

    for raw_query in ["iPhone%15", "кофе_", "кофе'"]:
        mock_db.execute = AsyncMock(side_effect=[count_result, list_result])
        result = await catalog_service.list_catalog_products(
            mock_db,
            search=raw_query,
            limit=20,
            offset=0,
        )
        assert result.total_count == 0
        assert result.items == []


async def test_special_chars_via_http_do_not_error(mock_db):
    """Спецсимволы через HTTP не вызывают 400/500 — проксируются как обычный запрос."""
    payload = ProductShortListResponse(items=[], total_count=0, limit=20, offset=0)

    async with await make_client() as client:
        with patch(
            "app.services.b2b_client.list_products",
            new_callable=AsyncMock,
            return_value=payload,
        ) as mocked:
            resp = await client.get("/api/v1/catalog/products", params={"q": "кофе'"})

    assert resp.status_code == 200
    assert mocked.await_args.kwargs["search"] == "кофе'"


async def test_empty_results_returns_200(mock_db):
    """Нет совпадений -> 200 с пустым items и total_count == 0."""
    count_result = MagicMock()
    count_result.scalar_one.return_value = 0
    list_result = MagicMock()
    list_result.all.return_value = []
    mock_db.execute = AsyncMock(side_effect=[count_result, list_result])

    result = await catalog_service.list_catalog_products(
        mock_db,
        search="несуществующий_товар_xyz",
        limit=20,
        offset=0,
    )

    assert result.total_count == 0
    assert result.items == []


async def test_empty_results_via_http_returns_200(mock_db):
    """B2C-эндпоинт возвращает 200 с пустым списком при отсутствии совпадений."""
    payload = ProductShortListResponse(items=[], total_count=0, limit=20, offset=0)

    async with await make_client() as client:
        with patch(
            "app.services.b2b_client.list_products",
            new_callable=AsyncMock,
            return_value=payload,
        ):
            resp = await client.get("/api/v1/catalog/products", params={"q": "несуществующий товар"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total_count"] == 0


# ── B2B-7 endpoint (/api/v1/products, X-Service-Key) ────────────────────────────


def _patch_visible_products(monkeypatch, products, total):
    async def fake_list_visible_products(db, **kwargs):
        return products, total

    monkeypatch.setattr(catalog_service, "list_visible_products", fake_list_visible_products)
    monkeypatch.setattr(
        "app.routers.products.list_visible_products", fake_list_visible_products
    )


async def test_b2b7_short_query_returns_400(mock_db):
    """GET /api/v1/products?search=ip (B2B-7) -> 400 INVALID_REQUEST для короткого запроса."""
    async with await make_client() as client:
        resp = await client.get(
            "/api/v1/products",
            params={"search": "ip"},
            headers=SERVICE_HEADERS,
        )

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "INVALID_REQUEST"