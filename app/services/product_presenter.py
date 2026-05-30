from __future__ import annotations

import uuid
from typing import Any

from app.models.product import Product, SKU
from app.schemas.product import (
    B2CCharacteristic,
    B2CProductImage,
    B2CProductResponse,
    B2CSkuResponse,
    BlockingReasonDetail,
    CatalogCategoryRef,
    CatalogImageRef,
    CatalogProductDetail,
    CatalogSellerRef,
    CatalogSku,
    CharacteristicResponse,
    FieldReportResponse,
    ProductImageResponse,
    ProductPublicResponse,
    ProductResponse,
    SKUPublicResponse,
    SKUImageResponse,
    SKUResponse,
)


def _characteristics_from_json(items: list[dict[str, Any]]) -> list[CharacteristicResponse]:
    return [CharacteristicResponse.model_validate(item) for item in items]


def _blocking_reason_from_product(product: Product) -> BlockingReasonDetail | None:
    if product.blocking_reason:
        return BlockingReasonDetail.model_validate(product.blocking_reason)
    if product.blocking_reason_id is not None:
        return BlockingReasonDetail(
            id=product.blocking_reason_id,
            title=product.moderator_comment or "",
            comment=product.moderator_comment,
        )
    return None


def _field_reports_from_product(product: Product) -> list[FieldReportResponse]:
    return [FieldReportResponse.model_validate(r) for r in (product.field_reports or [])]


def _sku_characteristics(sku: SKU) -> list[CharacteristicResponse]:
    return [
        CharacteristicResponse(id=ch.id, name=ch.name, value=ch.value)
        for ch in sku.characteristics_rel
    ]


def _sku_images(sku: SKU) -> list[SKUImageResponse]:
    return [SKUImageResponse.model_validate(img) for img in sku.images_rel]


def sku_to_seller_response(sku: SKU) -> SKUResponse:
    return SKUResponse(
        id=sku.id,
        product_id=sku.product_id,
        name=sku.name,
        price=sku.price,
        discount=sku.discount,
        cost_price=sku.cost_price,
        stock_quantity=sku.stock_quantity,
        active_quantity=sku.active_quantity,
        reserved_quantity=sku.reserved_quantity,
        article=sku.article,
        images=_sku_images(sku),
        characteristics=_sku_characteristics(sku),
        created_at=sku.created_at,
        updated_at=sku.updated_at,
    )


def sku_to_public_response(sku: SKU) -> SKUPublicResponse:
    return SKUPublicResponse(
        id=sku.id,
        product_id=sku.product_id,
        name=sku.name,
        price=sku.price,
        discount=sku.discount,
        stock_quantity=sku.stock_quantity,
        active_quantity=sku.active_quantity,
        article=sku.article,
        images=_sku_images(sku),
        characteristics=_sku_characteristics(sku),
    )


def product_to_seller_response(product: Product) -> ProductResponse:
    return ProductResponse(
        id=product.id,
        seller_id=product.seller_id,
        title=product.title,
        slug=product.slug,
        description=product.description,
        category_id=product.category_id,
        status=product.status.value,
        deleted=product.deleted,
        blocking_reason_id=product.blocking_reason_id,
        moderator_comment=product.moderator_comment,
        images=[ProductImageResponse.model_validate(img) for img in product.images],
        characteristics=_characteristics_from_json(product.characteristics),
        skus=[sku_to_seller_response(sku) for sku in product.skus],
        blocking_reason=_blocking_reason_from_product(product),
        field_reports=_field_reports_from_product(product),
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def product_to_public_response(product: Product) -> ProductPublicResponse:
    return ProductPublicResponse(
        id=product.id,
        seller_id=product.seller_id,
        title=product.title,
        slug=product.slug,
        description=product.description,
        category_id=product.category_id,
        status=product.status.value,
        images=[ProductImageResponse.model_validate(img) for img in product.images],
        characteristics=_characteristics_from_json(product.characteristics),
        skus=[sku_to_public_response(sku) for sku in product.skus],
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def _b2c_product_images(images: list[dict]) -> list[B2CProductImage]:
    return [B2CProductImage(url=img["url"], ordering=img["ordering"]) for img in images]


def _b2c_characteristics(items: list[dict]) -> list[B2CCharacteristic]:
    return [B2CCharacteristic(name=item["name"], value=item["value"]) for item in items]


def sku_to_b2c_response(sku: SKU) -> B2CSkuResponse:
    image: str | None = None
    if sku.images_rel:
        image = min(sku.images_rel, key=lambda img: img.ordering).url
    return B2CSkuResponse(
        id=sku.id,
        name=sku.name,
        price=sku.price,
        discount=sku.discount,
        image=image,
        active_quantity=sku.active_quantity,
        in_stock=sku.active_quantity > 0,
        characteristics=[B2CCharacteristic(name=ch.name, value=ch.value) for ch in sku.characteristics_rel],
    )


def product_to_b2c_response(product: Product) -> B2CProductResponse:
    return B2CProductResponse(
        id=product.id,
        slug=product.slug,
        title=product.title,
        description=product.description,
        images=_b2c_product_images(product.images),
        status=product.status.value,
        characteristics=_b2c_characteristics(product.characteristics),
        skus=[sku_to_b2c_response(sku) for sku in product.skus],
    )


# ── Catalog (B2C по спецификации) ───────────────────────────────────────

def _catalog_product_images(images: list[dict]) -> list[CatalogImageRef]:
    return [
        CatalogImageRef(
            id=uuid.UUID(img["id"]) if isinstance(img["id"], str) else img["id"],
            url=img["url"],
            ordering=img.get("ordering", 0),
        )
        for img in images
    ]


def _catalog_sku_images(sku: SKU) -> list[CatalogImageRef]:
    return [
        CatalogImageRef(
            id=img.id,
            url=img.url,
            ordering=img.ordering,
        )
        for img in sku.images_rel
    ]


def sku_to_catalog_response(sku: SKU) -> CatalogSku:
    chars = {ch.name: ch.value for ch in sku.characteristics_rel}
    return CatalogSku(
        id=sku.id,
        name=sku.name,
        sku_code=sku.article,
        price=sku.price,
        old_price=(sku.price + sku.discount) if sku.discount > 0 else None,
        available_quantity=sku.active_quantity,
        attributes=chars or None,
        images=_catalog_sku_images(sku),
    )


def product_to_catalog_detail(product: Product) -> CatalogProductDetail:
    prices = [sku.price for sku in product.skus] if product.skus else [0]
    min_price = min(prices)
    has_stock = any(sku.active_quantity > 0 for sku in product.skus)

    chars = {
        ch["name"]: ch["value"]
        for ch in (product.characteristics or [])
    } if product.characteristics else None

    # category — используем category_id если нет связанной сущности
    category = None
    if product.category_id:
        category = CatalogCategoryRef(
            id=product.category_id,
            name="",
            level=0,
            path=[],
        )

    # seller — используем seller_id с display_name из данных продукта
    seller = None
    if product.seller_id:
        seller = CatalogSellerRef(
            id=product.seller_id,
            display_name="",
        )

    return CatalogProductDetail(
        id=product.id,
        name=product.title,
        slug=product.slug,
        category=category,
        seller=seller,
        min_price=min_price,
        has_stock=has_stock,
        images=_catalog_product_images(product.images),
        description=product.description or "",
        attributes=chars,
        skus=[sku_to_catalog_response(sku) for sku in product.skus],
    )
