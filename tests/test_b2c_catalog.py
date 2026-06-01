"""
B2C-1: каталог с фильтрами, сортировкой и фасетами.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies.db import get_db
from app.main import app
from app.models.product import Product, ProductStatus
from app.schemas.catalog import FacetsResponse, ProductShortListResponse
from app.schemas.errors import VALID_SORTS
from app.services import catalog_service

CATEGORY_ID = uuid.uuid4()
PRODUCT_ID_1 = uuid.uuid4()
PRODUCT_ID_2 = uuid.uuid4()
_NOW = datetime.now(timezone.utc)


def make_product(
    *,
    product_id: uuid.UUID = PRODUCT_ID_1,
    title: str = "iPhone 15 Pro Max",
    price_kopecks: int = 12_999_000,
    characteristics: list | None = None,
    created_at: datetime | None = None,
) -> MagicMock:
    p = MagicMock(spec=Product)
    p.id = product_id
    p.title = title
    p.category_id = CATEGORY_ID
    p.status = ProductStatus.MODERATED
    p.deleted = False
    p.description = "Flagship"
    p.images = [{"url": "https://cdn.neomarket.ru/images/iphone15.jpg", "ordering": 0}]
    p.characteristics = characteristics or [{"name": "Бренд", "value": "Apple"}]
    p.skus = []
    p.created_at = created_at or _NOW
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


async def test_catalog_returns_filtered_sorted_products(mock_db):
    """Фильтр по категории, сортировка price_asc и пагинация."""
    products = [
        (make_product(product_id=PRODUCT_ID_1, title="A", created_at=_NOW), 8_999_000),
        (make_product(product_id=PRODUCT_ID_2, title="B", created_at=_NOW), 12_999_000),
    ]

    count_result = MagicMock()
    count_result.scalar_one.return_value = 2
    list_result = MagicMock()
    list_result.all.return_value = products

    mock_db.execute = AsyncMock(side_effect=[count_result, list_result])

    result = await catalog_service.list_catalog_products(
        mock_db,
        category_id=CATEGORY_ID,
        sort="price_asc",
        limit=20,
        offset=0,
    )

    assert result.total_count == 2
    assert result.limit == 20
    assert result.offset == 0
    assert len(result.items) == 2
    assert result.items[0].price == 8_999_000
    assert result.items[0].title == "A"
    assert result.items[0].in_stock is True
    assert result.items[1].price == 12_999_000
    assert mock_db.execute.await_count == 2


async def test_facets_return_counts_per_filter_value(mock_db):
    products = [
        (make_product(characteristics=[{"name": "Бренд", "value": "Apple"}]), 10_000),
        (make_product(
            product_id=PRODUCT_ID_2,
            characteristics=[{"name": "Бренд", "value": "Apple"}],
        ), 11_000),
        (make_product(
            product_id=uuid.uuid4(),
            characteristics=[{"name": "Бренд", "value": "Samsung"}],
        ), 9_000),
    ]
    list_result = MagicMock()
    list_result.all.return_value = products
    mock_db.execute = AsyncMock(return_value=list_result)

    result = await catalog_service.get_catalog_facets(
        mock_db,
        category_id=CATEGORY_ID,
    )

    brand_facet = next(f for f in result.facets if f.name == "бренд")
    counts = {v.value: v.count for v in brand_facet.values}
    assert counts["Apple"] == 2
    assert counts["Samsung"] == 1
    assert result.category_id == CATEGORY_ID


# ── Edge cases ────────────────────────────────────────────────────────────────


async def test_invalid_sort_returns_400():
    async with await make_client() as client:
        resp = await client.get("/api/v1/catalog/products", params={"sort": "not_a_sort"})

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "INVALID_REQUEST"
    assert "Invalid sort parameter" in body["message"]
    for allowed in VALID_SORTS:
        assert allowed in body["message"]


async def test_b2b_unavailable_returns_502():
    async with await make_client() as client:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        with patch("app.services.b2b_client.httpx.AsyncClient") as client_cls:
            client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            resp = await client.get(
                "/api/v1/catalog/products",
                params={"category_id": str(CATEGORY_ID), "sort": "rating"},
            )

    assert resp.status_code == 502
    body = resp.json()
    assert body["code"] == "B2B_UNAVAILABLE"


async def test_b2c_proxy_returns_b2b_payload():
    payload = ProductShortListResponse(
        items=[],
        total_count=0,
        limit=20,
        offset=0,
    )
    async with await make_client() as client:
        with patch(
            "app.services.b2b_client.list_products",
            new_callable=AsyncMock,
            return_value=payload,
        ) as mocked:
            resp = await client.get(
                "/api/v1/catalog/products",
                params={"category_id": str(CATEGORY_ID), "sort": "rating"},
            )

    assert resp.status_code == 200
    mocked.assert_awaited_once()
    call_kwargs = mocked.await_args.kwargs
    assert call_kwargs["category_id"] == CATEGORY_ID
    assert call_kwargs["sort"] == "rating"
