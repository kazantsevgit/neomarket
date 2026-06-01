"""
B2B-7: GET /api/v1/products — каталог для B2C (X-Service-Key).

Использует реальную in-memory SQLite (aiosqlite) без подмены реализации БД.
"""
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.dependencies.db import get_db
from app.main import app
from app.database import Base
from app.models.product import Product, ProductStatus, SKU

SELLER_ID = uuid.uuid4()
CATEGORY_ID = uuid.uuid4()
VISIBLE_ID = uuid.uuid4()
HARD_BLOCKED_ID = uuid.uuid4()
OUT_OF_STOCK_ID = uuid.uuid4()
DELETED_ID = uuid.uuid4()
HIDDEN_BATCH_ID = uuid.uuid4()
_NOW = datetime.now(timezone.utc)

SERVICE_HEADERS = {"X-Service-Key": settings.B2B_SERVICE_KEY}


@pytest.fixture(scope="session")
def _engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    return engine


@pytest.fixture(scope="function")
async def db_session(_engine):
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(_engine, expire_on_commit=False)

    async with async_session() as session:
        await _seed(session)
        yield session

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _seed(session: AsyncSession):
    """Наполнение таблиц тестовыми данными."""
    products = [
        Product(
            id=VISIBLE_ID,
            seller_id=SELLER_ID,
            title="iPhone 15 Pro Max",
            slug="iphone-15-pro-max",
            description="Flagship",
            category_id=CATEGORY_ID,
            status=ProductStatus.MODERATED,
            deleted=False,
            images=[{"id": str(uuid.uuid4()), "url": "/s3/front.jpg", "ordering": 0}],
            characteristics=[{"id": str(uuid.uuid4()), "name": "Бренд", "value": "Apple"}],
            created_at=_NOW,
            updated_at=_NOW,
        ),
        Product(
            id=HARD_BLOCKED_ID,
            seller_id=SELLER_ID,
            title="Samsung Galaxy S24",
            slug="samsung-galaxy-s24",
            description="Blocked",
            category_id=CATEGORY_ID,
            status=ProductStatus.HARD_BLOCKED,
            deleted=False,
            images=[],
            characteristics=[],
            created_at=_NOW,
            updated_at=_NOW,
        ),
        Product(
            id=OUT_OF_STOCK_ID,
            seller_id=SELLER_ID,
            title="Out of stock",
            slug="out-of-stock",
            description="No stock",
            category_id=CATEGORY_ID,
            status=ProductStatus.MODERATED,
            deleted=False,
            images=[],
            characteristics=[],
            created_at=_NOW,
            updated_at=_NOW,
        ),
        Product(
            id=DELETED_ID,
            seller_id=SELLER_ID,
            title="Deleted product",
            slug="deleted-product",
            description="Deleted",
            category_id=CATEGORY_ID,
            status=ProductStatus.MODERATED,
            deleted=True,
            images=[],
            characteristics=[],
            created_at=_NOW,
            updated_at=_NOW,
        ),
        Product(
            id=HIDDEN_BATCH_ID,
            seller_id=SELLER_ID,
            title="Blocked product",
            slug="blocked-product",
            description="Blocked",
            category_id=CATEGORY_ID,
            status=ProductStatus.BLOCKED,
            deleted=False,
            images=[],
            characteristics=[],
            created_at=_NOW,
            updated_at=_NOW,
        ),
    ]
    for p in products:
        session.add(p)
    await session.flush()

    skus = [
        SKU(
            id=uuid.uuid4(),
            product_id=VISIBLE_ID,
            name="256GB Black",
            price=12_999_000,
            discount=0,
            cost_price=9_500_000,
            stock_quantity=10,
            reserved_quantity=0,
            created_at=_NOW,
            updated_at=_NOW,
        ),
        SKU(
            id=uuid.uuid4(),
            product_id=HARD_BLOCKED_ID,
            name="128GB Gray",
            price=10_000_000,
            discount=0,
            cost_price=7_000_000,
            stock_quantity=5,
            reserved_quantity=0,
            created_at=_NOW,
            updated_at=_NOW,
        ),
        SKU(
            id=uuid.uuid4(),
            product_id=OUT_OF_STOCK_ID,
            name="64GB White",
            price=8_000_000,
            discount=0,
            cost_price=5_000_000,
            stock_quantity=2,
            reserved_quantity=2,
            created_at=_NOW,
            updated_at=_NOW,
        ),
        SKU(
            id=uuid.uuid4(),
            product_id=DELETED_ID,
            name="128GB Blue",
            price=9_000_000,
            discount=0,
            cost_price=6_000_000,
            stock_quantity=3,
            reserved_quantity=0,
            created_at=_NOW,
            updated_at=_NOW,
        ),
        SKU(
            id=uuid.uuid4(),
            product_id=HIDDEN_BATCH_ID,
            name="256GB Red",
            price=11_000_000,
            discount=0,
            cost_price=8_000_000,
            stock_quantity=4,
            reserved_quantity=0,
            created_at=_NOW,
            updated_at=_NOW,
        ),
    ]
    for s in skus:
        session.add(s)
    await session.commit()


@pytest.fixture
async def db_override(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_catalog_returns_moderated_in_stock_products(db_override, client):
    async with client as ac:
        resp = await ac.get("/api/v1/products", headers=SERVICE_HEADERS)

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["id"] == str(VISIBLE_ID)
    assert item["status"] == "MODERATED"
    assert item["min_price"] == 12_999_000


async def test_catalog_excludes_hard_blocked(db_override, client):
    async with client as ac:
        resp = await ac.get("/api/v1/products", headers=SERVICE_HEADERS)

    ids = {item["id"] for item in resp.json()["items"]}
    assert str(HARD_BLOCKED_ID) not in ids
    assert str(OUT_OF_STOCK_ID) not in ids
    assert str(DELETED_ID) not in ids
    assert str(HIDDEN_BATCH_ID) not in ids


async def test_catalog_missing_service_key_returns_401(client):
    async with client as ac:
        resp = await ac.get("/api/v1/products")

    assert resp.status_code == 401


async def test_catalog_invalid_service_key_returns_401(client):
    async with client as ac:
        resp = await ac.get(
            "/api/v1/products",
            headers={"X-Service-Key": "wrong-key"},
        )

    assert resp.status_code == 401


async def test_catalog_response_has_no_cost_price(db_override, client):
    async with client as ac:
        resp = await ac.get("/api/v1/products", headers=SERVICE_HEADERS)

    body_str = resp.text
    assert "cost_price" not in body_str
    assert "reserved_quantity" not in body_str


async def test_batch_ids_returns_visible_subset(db_override, db_session, client):
    batch_ids = [VISIBLE_ID, HARD_BLOCKED_ID, HIDDEN_BATCH_ID]
    ids_param = ",".join(str(i) for i in batch_ids)

    async with client as ac:
        resp = await ac.get(
            f"/api/v1/products?ids={ids_param}",
            headers=SERVICE_HEADERS,
        )

    assert resp.status_code == 200
    returned_ids = {item["id"] for item in resp.json()["items"]}
    assert returned_ids == {str(VISIBLE_ID)}
    assert str(HARD_BLOCKED_ID) not in returned_ids
    assert str(HIDDEN_BATCH_ID) not in returned_ids


async def test_catalog_response_matches_short_schema(db_override, client):
    async with client as ac:
        resp = await ac.get("/api/v1/products", headers=SERVICE_HEADERS)

    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total_count" in data
    assert "limit" in data
    assert "offset" in data
    assert isinstance(data["items"], list)

    if data["items"]:
        item = data["items"][0]
        required = {"id", "title", "slug", "status", "category_id", "created_at", "min_price"}
        assert required.issubset(item.keys()), f"Missing fields: {required - item.keys()}"
        assert "cost_price" not in item
        assert "skus" not in item
