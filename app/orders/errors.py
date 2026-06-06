"""Единый формат ошибок B2C Orders: плоский {code, message, ...}."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def order_http_error(status_code: int, code: str, message: str, **extra: Any) -> HTTPException:
    """HTTPException с телом {code, message} — разворачивается в app.main."""
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **extra})
