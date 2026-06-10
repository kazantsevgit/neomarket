"""
B2B-11: GET /api/v1/products — список товаров продавца (seller cabinet, JWT).

Тесты используют реальную in-memory SQLite и валидный JWT.
"""
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.dependencies.db import get_db
from app.main import app
from app.database import Base
from app.models.product import Product, ProductStatus, SKU

SELLER_A = uuid.uuid4()
SELLER_B = uuid.uuid4()
CATEGORY_ID = uuid.uuid4()

PROD_A1_ID = uuid.uuid4()
PROD_A2_ID = uuid.uuid4()
PROD_A3_ID = uuid.uuid4()
PROD_A4_DELETED_ID = uuid.uuid4()
PROD_B1_ID = uuid.uuid4()

_NOW = datetime.now(timezone.utc)


def _seller_jwt(seller_id: uuid.UUID) -> dict:
    token = jwt.encode(
        {"sub": str(seller_id)},
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


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
    products = [
        Product(
            id=PROD_A1_ID,
            seller_id=SELLER_A,
            title="iPhone 15 Pro Max",
            slug="iphone-15-pro-max",
            description="Flagship",
            category_id=CATEGORY_ID,
            status=ProductStatus.MODERATED,
            deleted=False,
            images=[{"id": str(uuid.uuid4()), "url": "/s3/iphone15-front.jpg", "ordering": 0}],
            characteristics=[],
            created_at=_NOW,
            updated_at=_NOW,
        ),
        Product(
            id=PROD_A2_ID,
            seller_id=SELLER_A,
            title="Samsung Galaxy S24",
            slug="samsung-galaxy-s24",
            description="Android flagship",
            category_id=CATEGORY_ID,
            status=ProductStatus.BLOCKED,
            deleted=False,
            images=[],
            characteristics=[],
            created_at=_NOW,
            updated_at=_NOW,
        ),
        Product(
            id=PROD_A3_ID,
            seller_id=SELLER_A,
            title="Google Pixel 8",
            slug="google-pixel-8",
            description="Stock Android",
            category_id=CATEGORY_ID,
            status=ProductStatus.CREATED,
            deleted=False,
            images=[],
            characteristics=[],
            created_at=_NOW,
            updated_at=_NOW,
        ),
        Product(
            id=PROD_A4_DELETED_ID,
            seller_id=SELLER_A,
            title="OnePlus 12",
            slug="oneplus-12",
            description="Deleted",
            category_id=CATEGORY_ID,
            status=ProductStatus.ON_MODERATION,
            deleted=True,
            images=[],
            characteristics=[],
            created_at=_NOW,
            updated_at=_NOW,
        ),
        Product(
            id=PROD_B1_ID,
            seller_id=SELLER_B,
            title="Xiaomi 14",
            slug="xiaomi-14",
            description="Another seller",
            category_id=CATEGORY_ID,
            status=ProductStatus.MODERATED,
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
            product_id=PROD_A1_ID,
            name="256GB Black",
            price=12_999_000,
            discount=0,
            cost_price=9_500_000,
            stock_quantity=10,
            reserved_quantity=1,
            created_at=_NOW,
            updated_at=_NOW,
        ),
        SKU(
            id=uuid.uuid4(),
            product_id=PROD_A1_ID,
            name="512GB Black",
            price=14_999_000,
            discount=0,
            cost_price=11_000_000,
            stock_quantity=5,
            reserved_quantity=0,
            created_at=_NOW,
            updated_at=_NOW,
        ),
        SKU(
            id=uuid.uuid4(),
            product_id=PROD_A2_ID,
            name="128GB Gray",
            price=10_000_000,
            discount=0,
            cost_price=7_000_000,
            stock_quantity=3,
            reserved_quantity=0,
            created_at=_NOW,
            updated_at=_NOW,
        ),
        SKU(
            id=uuid.uuid4(),
            product_id=PROD_A3_ID,
            name="128GB White",
            price=8_000_000,
            discount=0,
            cost_price=5_500_000,
            stock_quantity=2,
            reserved_quantity=0,
            created_at=_NOW,
            updated_at=_NOW,
        ),
        SKU(
            id=uuid.uuid4(),
            product_id=PROD_A4_DELETED_ID,
            name="256GB Green",
            price=11_000_000,
            discount=0,
            cost_price=8_000_000,
            stock_quantity=4,
            reserved_quantity=0,
            created_at=_NOW,
            updated_at=_NOW,
        ),
        SKU(
            id=uuid.uuid4(),
            product_id=PROD_B1_ID,
            name="128GB Blue",
            price=9_000_000,
            discount=0,
            cost_price=6_000_000,
            stock_quantity=7,
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


async def test_list_returns_only_own_products(db_override, client):
    """Только товары с seller_id из JWT (SELLER_A)."""
    async with client as ac:
        resp = await ac.get("/api/v1/products", headers=_seller_jwt(SELLER_A))

    assert resp.status_code == 200
    data = resp.json()
    returned_ids = {item["id"] for item in data["items"]}
    assert str(PROD_A1_ID) in returned_ids
    assert str(PROD_A2_ID) in returned_ids
    assert str(PROD_A3_ID) in returned_ids
    assert str(PROD_B1_ID) not in returned_ids
    assert data["total_count"] == 3  # A1, A2, A3; A4 deleted excluded
    assert len(data["items"]) == 3


async def test_idor_query_param_seller_id_ignored(db_override, client):
    """?seller_id= в query не меняет выборку — seller_id только из JWT."""
    headers = _seller_jwt(SELLER_A)
    async with client as ac:
        resp = await ac.get(
            "/api/v1/products?seller_id=" + str(SELLER_B),
            headers=headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    returned_ids = {item["id"] for item in data["items"]}
    assert str(PROD_B1_ID) not in returned_ids
    assert str(PROD_A1_ID) in returned_ids
    assert data["total_count"] == 3


async def test_deleted_products_visible_with_deleted_flag(db_override, client):
    """Удалённые товары видны с include_deleted=true."""
    headers = _seller_jwt(SELLER_A)
    async with client as ac:
        resp = await ac.get("/api/v1/products?include_deleted=true", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    returned_ids = {item["id"] for item in data["items"]}
    assert str(PROD_A4_DELETED_ID) in returned_ids
    assert data["total_count"] == 4  # all of SELLER_A's products


async def test_status_filter_works_correctly(db_override, client):
    """?status=BLOCKED возвращает только BLOCKED."""
    headers = _seller_jwt(SELLER_A)
    async with client as ac:
        resp = await ac.get("/api/v1/products?status=BLOCKED", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == str(PROD_A2_ID)
    assert data["items"][0]["status"] == "BLOCKED"
    assert data["total_count"] == 1


async def test_search_by_title_case_insensitive(db_override, client):
    """Поиск нечувствителен к регистру."""
    headers = _seller_jwt(SELLER_A)
    async with client as ac:
        resp = await ac.get("/api/v1/products?search=iphone", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == str(PROD_A1_ID)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/products?search=IPHONE", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == str(PROD_A1_ID)


async def test_response_has_skus_count_and_total_active_quantity(db_override, client):
    """Ответ включает skus_count и total_active_quantity для каждого товара."""
    headers = _seller_jwt(SELLER_A)
    async with client as ac:
        resp = await ac.get("/api/v1/products", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    prod_a1 = next(item for item in data["items"] if item["id"] == str(PROD_A1_ID))
    assert prod_a1["skus_count"] == 2
    assert prod_a1["total_active_quantity"] == 14  # (10-1) + (5-0) = 14


async def test_unauthorized_without_jwt_returns_401(client):
    """Запрос без JWT возвращает 401."""
    async with client as ac:
        resp = await ac.get("/api/v1/products")

    assert resp.status_code == 401


async def test_response_has_required_fields(db_override, client):
    """Проверка структуры ответа."""
    headers = _seller_jwt(SELLER_A)
    async with client as ac:
        resp = await ac.get("/api/v1/products", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total_count" in data
    assert "limit" in data
    assert "offset" in data

    if data["items"]:
        item = data["items"][0]
        required = {"id", "title", "slug", "status", "category_id", "deleted", "created_at", "skus_count", "total_active_quantity"}
        assert required.issubset(item.keys()), f"Missing: {required - item.keys()}"
