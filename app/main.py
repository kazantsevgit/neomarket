from fastapi import FastAPI
from app.routers.products import router as products_router
from app.routers.skus import router as skus_router
from app.routers.inventory import router as inventory_router

app = FastAPI(title="NeoMarket B2B API", version="1.0.0")

app.include_router(products_router)
app.include_router(skus_router)
app.include_router(inventory_router)