from fastapi import HTTPException, status


VALID_SORTS: tuple[str, ...] = (
    "price_asc",
    "price_desc",
    "popularity",
    "new",
)


def invalid_sort_error() -> HTTPException:
    allowed = ", ".join(VALID_SORTS)
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "INVALID_REQUEST",
            "message": f"Invalid sort parameter. Allowed: {allowed}",
        },
    )


def invalid_request(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "INVALID_REQUEST", "message": message},
    )


def b2b_unavailable_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={
            "code": "B2B_UNAVAILABLE",
            "message": "Catalog temporarily unavailable, please try later",
        },
    )
