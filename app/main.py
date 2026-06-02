from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.routers.catalog import router as catalog_router
from app.routers.products import router as products_router
from app.routers.public import router as public_router
from app.routers.skus import router as skus_router
from app.routers.inventory import router as inventory_router
from app.routers.moderation import router as moderation_router
from app.routers.decline import router as decline_router
from app.routers.favorites import router as favorites_router

from app.routers.orders import router as orders_router
from app.routers.cart import router as cart_router
from app.routers.auth import router as auth_router

app = FastAPI(title="NeoMarket B2B API", version="1.0.0")

app.include_router(catalog_router)
app.include_router(products_router)
app.include_router(public_router)
app.include_router(skus_router)
app.include_router(inventory_router)
app.include_router(moderation_router)
app.include_router(decline_router)
app.include_router(favorites_router)
app.include_router(orders_router)
app.include_router(cart_router)
app.include_router(auth_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "code" in exc.detail and "message" in exc.detail:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail,
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": "ERROR", "message": str(exc.detail)},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    message = "Invalid request"
    if errors:
        err = errors[0]
        field = ".".join([str(loc) for loc in err["loc"] if loc != "body"])
        message = f"{field} {err['msg']}".strip()
        if "category_id" in field and "missing" in err["msg"].lower():
            message = "category_id is required"
        elif "title" in field and "missing" in err["msg"].lower():
            message = "title is required"
        elif "images" in field and "missing" in err["msg"].lower():
            message = "At least one image is required"

    return JSONResponse(
        status_code=422,
        content={"code": "VALIDATION_ERROR", "message": message},
    )
