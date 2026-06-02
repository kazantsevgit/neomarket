import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings

moderator_bearer = HTTPBearer()


def get_current_moderator_id(
    credentials: HTTPAuthorizationCredentials = Depends(moderator_bearer),
) -> uuid.UUID:
    """ID модератора из JWT (claim sub)."""
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        moderator_id = payload.get("sub")
        if not moderator_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "Invalid token: missing sub"},
            )
        return uuid.UUID(str(moderator_id))
    except (JWTError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Could not validate token"},
        )
