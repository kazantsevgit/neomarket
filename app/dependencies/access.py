import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from app.config import settings
from app.dependencies.auth import get_optional_current_seller_id

class ProductAccessMode(str, Enum):
    SELLER = "seller"
    SERVICE = "service"


@dataclass(frozen=True)
class ProductAccess:
    mode: ProductAccessMode
    seller_id: Optional[uuid.UUID] = None


async def resolve_product_access(
    x_service_key: Optional[str] = Header(None, alias="X-Service-Key"),
    seller_id: Optional[uuid.UUID] = Depends(get_optional_current_seller_id),
) -> ProductAccess:
    """Один endpoint GET /products/{id}: seller JWT или валидный X-Service-Key."""
    if x_service_key is not None:
        if x_service_key != settings.B2B_SERVICE_KEY:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid service key",
            )
        return ProductAccess(mode=ProductAccessMode.SERVICE)

    if seller_id is not None:
        return ProductAccess(mode=ProductAccessMode.SELLER, seller_id=seller_id)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authorization required",
    )
