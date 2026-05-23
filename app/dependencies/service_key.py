from fastapi import Header, HTTPException, status

from app.config import settings


async def require_catalog_service_key(
    x_service_key: str | None = Header(None, alias="X-Service-Key"),
) -> None:
    """B2B-7: каталог доступен только с валидным X-Service-Key (без JWT)."""
    if x_service_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization required",
        )
    if x_service_key != settings.B2B_SERVICE_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service key",
        )
