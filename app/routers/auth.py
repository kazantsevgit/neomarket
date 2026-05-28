"""
Auth router (минимально для US-CART-03):
POST /api/v1/auth/login

По OpenAPI: если передан X-Session-Id, при логине выполняется merge гостевой корзины
в пользовательскую по правилу max(quantity).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, status, Depends
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies.db import get_db
from app.schemas.auth import LoginRequest, TokenResponse
from app.services import cart_service


router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _issue_jwt(*, user_id: uuid.UUID, ttl_seconds: int) -> str:
    now = _utcnow()
    payload = {
        "sub": str(user_id),
        "role": "buyer",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


@router.post("/login", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def login(
    body: LoginRequest,
    x_session_id: uuid.UUID | None = Header(None, alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    # Минимальная реализация без БД пользователей:
    user_id = uuid.uuid5(uuid.NAMESPACE_DNS, body.email.lower())

    # Merge гостевой корзины в пользовательскую — строго как в OpenAPI и каноне.
    if x_session_id is not None:
        await cart_service.merge_guest_into_user(db, user_id=user_id, guest_session_id=x_session_id)

    access_ttl = 3600
    refresh_ttl = 30 * 24 * 3600

    return TokenResponse(
        user_id=user_id,
        access_token=_issue_jwt(user_id=user_id, ttl_seconds=access_ttl),
        refresh_token=_issue_jwt(user_id=user_id, ttl_seconds=refresh_ttl),
        token_type="Bearer",
        expires_in=access_ttl,
    )

