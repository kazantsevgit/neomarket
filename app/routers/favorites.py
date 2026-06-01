import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_user_id
from app.dependencies.db import get_db
from app.schemas.favorite import PaginatedCatalogProducts
from app.services.favorite_service import add_favorite, get_favorites, remove_favorite

router = APIRouter(prefix="/api/v1/favorites", tags=["Favorites"])


@router.get("", response_model=PaginatedCatalogProducts)
async def list_favorites(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> PaginatedCatalogProducts:
    return await get_favorites(db=db, user_id=user_id, limit=limit, offset=offset)


@router.put("/{product_id}")
async def add_favorite_endpoint(
    product_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> Response:
    created = await add_favorite(db=db, user_id=user_id, product_id=product_id)
    if created:
        return Response(status_code=status.HTTP_201_CREATED)
    return Response(status_code=status.HTTP_200_OK)


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def remove_favorite_endpoint(
    product_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    await remove_favorite(db=db, user_id=user_id, product_id=product_id)
