import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ── Вспомогательные схемы ─────────────────────────────────────────────────────

class ProductImageCreate(BaseModel):
    url: str
    ordering: int = 0


class ProductImageResponse(BaseModel):
    id: uuid.UUID
    url: str
    ordering: int


class Characteristic(BaseModel):
    name: str
    value: str


class CharacteristicResponse(Characteristic):
    id: uuid.UUID


class BlockingReasonDetail(BaseModel):
    id: uuid.UUID
    title: str
    comment: Optional[str] = None


class FieldReportResponse(BaseModel):
    field_name: str
    sku_id: Optional[uuid.UUID] = None
    comment: str


# ── SKU ──────────────────────────────────────────────────────────────────────

class SKUImageCreate(BaseModel):
    url: str
    ordering: int = 0


class SKUImageResponse(BaseModel):
    id: uuid.UUID
    url: str
    ordering: int

    model_config = {"from_attributes": True}


class SKUCreate(BaseModel):
    product_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=255)   # блокер 2: обязательное поле
    price: int  = Field(..., ge=0, description="Цена в копейках")
    discount:   int  = Field(default=0, ge=0, description="Скидка в копейках")
    cost_price: Optional[int]  = Field(default=None, description="Себестоимость в копейках")
    article:    Optional[str]  = None
    images:     List[SKUImageCreate]     = Field(default_factory=list)
    characteristics: List[Characteristic] = Field(default_factory=list)


class SKUUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    price: int = Field(..., ge=0, description="Цена в копейках")
    discount: int = Field(default=0, ge=0, description="Скидка в копейках")
    cost_price: Optional[int] = Field(default=None)
    article: Optional[str] = None
    images: List[SKUImageCreate] = Field(default_factory=list)
    characteristics: List[Characteristic] = Field(default_factory=list)


class SKUResponse(BaseModel):
    """Seller-view SKU — соответствует neomarket-b2b.yaml:1284-1318."""
    id:               uuid.UUID
    product_id:       uuid.UUID
    name:             str
    price:            int
    discount:         int
    cost_price:       Optional[int]
    stock_quantity:   int
    active_quantity:  int
    reserved_quantity: int
    article:          Optional[str]
    images:           List[SKUImageResponse]
    characteristics:  List[CharacteristicResponse]
    created_at:       datetime
    updated_at:       datetime

    model_config = {"from_attributes": True}


class SKUPublicResponse(BaseModel):
    """Витринный SKU — без cost_price и reserved_quantity (neomarket-b2b.yaml)."""
    id: uuid.UUID
    product_id: uuid.UUID
    name: str
    price: int
    discount: int
    stock_quantity: int
    active_quantity: int
    article: Optional[str] = None
    images: List[SKUImageResponse]
    characteristics: List[CharacteristicResponse]

    model_config = {"from_attributes": True}


# ── Product ───────────────────────────────────────────────────────────────────

class ProductCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1, max_length=5000)
    category_id: uuid.UUID
    characteristics: List[Characteristic] = Field(default_factory=list)
    images: List[ProductImageCreate] = Field(..., min_length=1)

    @field_validator("images")
    @classmethod
    def images_not_empty(cls, v: List[ProductImageCreate]) -> List[ProductImageCreate]:
        if not v:
            raise ValueError("images must contain at least one image")
        return v


class ProductUpdate(BaseModel):
    """Схема для обновления товара (PATCH /products/{id})."""
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1, max_length=5000)
    category_id: uuid.UUID
    characteristics: List[Characteristic] = Field(default_factory=list)
    images: List[ProductImageCreate] = Field(..., min_length=1)

    @field_validator("images")
    @classmethod
    def images_not_empty(cls, v: List[ProductImageCreate]) -> List[ProductImageCreate]:
        if not v:
            raise ValueError("images must contain at least one image")
        return v

class ProductResponse(BaseModel):
    id: uuid.UUID
    seller_id: uuid.UUID
    title: str
    slug: str
    description: Optional[str]
    category_id: uuid.UUID
    status: str
    deleted: bool
    blocking_reason_id: Optional[uuid.UUID] = None
    moderator_comment: Optional[str] = None
    images: List[ProductImageResponse]
    characteristics: List[CharacteristicResponse]
    skus: List[SKUResponse] = []
    blocking_reason: Optional[BlockingReasonDetail] = None
    field_reports: List[FieldReportResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProductPublicResponse(BaseModel):
    """Межсервисный / B2C view — без seller-only полей у SKU."""
    id: uuid.UUID
    seller_id: uuid.UUID
    title: str
    slug: str
    description: Optional[str]
    category_id: uuid.UUID
    status: str
    images: List[ProductImageResponse]
    characteristics: List[CharacteristicResponse]
    skus: List[SKUPublicResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProductPublicShortResponse(BaseModel):
    """Короткая карточка товара для списка каталога (neomarket-b2b.yaml ProductPublicShortResponse)."""
    id: uuid.UUID
    title: str
    slug: str
    status: str
    category_id: uuid.UUID
    min_price: int
    cover_image: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProductShortResponse(BaseModel):
    """Короткая карточка товара для списка продавца (seller cabinet).
    Соответствует neomarket-b2b.yaml ProductShortResponse + skus_count, total_active_quantity.
    """
    id: uuid.UUID
    title: str
    slug: str
    status: str
    category_id: uuid.UUID
    deleted: bool
    created_at: datetime
    min_price: int | None = None
    cover_image: str | None = None
    skus_count: int = 0
    total_active_quantity: int = 0

    model_config = {"from_attributes": True}


class ProductPaginatedResponse(BaseModel):
    items: List[ProductShortResponse]
    total_count: int
    limit: int
    offset: int


class ProductPublicPaginatedResponse(BaseModel):
    items: List[ProductPublicShortResponse]
    total_count: int
    limit: int
    offset: int


# ── B2C Catalog (canonic flow B2C-3) ──────────────────────────────────────────

class B2CCharacteristic(BaseModel):
    name: str
    value: str


class B2CProductImage(BaseModel):
    url: str
    ordering: int


class B2CSkuResponse(BaseModel):
    id: uuid.UUID
    name: str
    price: int
    discount: int
    image: Optional[str] = None
    active_quantity: int
    in_stock: bool
    characteristics: List[B2CCharacteristic]


class B2CProductResponse(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    description: Optional[str] = None
    images: List[B2CProductImage]
    status: str
    characteristics: List[B2CCharacteristic]
    skus: List[B2CSkuResponse]


# ── Catalog (B2C по спецификации neomarket-b2c.yaml) ─────────────────────────

class CatalogImageRef(BaseModel):
    """ImageRef из спецификации — обязательные id, url, ordering."""
    id: uuid.UUID
    url: str
    alt: Optional[str] = None
    ordering: int = 0
    is_main: bool = False


class CatalogCategoryRef(BaseModel):
    """CategoryRef — только доступные поля из Category."""
    id: uuid.UUID
    name: str
    level: int = 0
    path: list[str] = []


class CatalogSellerRef(BaseModel):
    """SellerRef для CatalogProductCard."""
    id: uuid.UUID
    display_name: str


class CatalogSku(BaseModel):
    """CatalogSku из B2C-спецификации."""
    id: uuid.UUID
    name: str
    sku_code: Optional[str] = None
    price: int
    old_price: Optional[int] = None
    available_quantity: int
    attributes: Optional[Dict[str, Any]] = None
    images: List[CatalogImageRef] = []


class CatalogProductCard(BaseModel):
    """CatalogProductCard из B2C-спецификации."""
    id: uuid.UUID
    name: str
    slug: str
    category: Optional[CatalogCategoryRef] = None
    min_price: int
    old_price: Optional[int] = None
    has_stock: bool
    rating: Optional[float] = None
    reviews_count: int = 0
    images: List[CatalogImageRef] = []
    seller: Optional[CatalogSellerRef] = None


class CatalogProductDetail(CatalogProductCard):
    """CatalogProductDetail = CatalogProductCard + description + skus."""
    description: str
    attributes: Optional[Dict[str, Any]] = None
    skus: List[CatalogSku] = []
