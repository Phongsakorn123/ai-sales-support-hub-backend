from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.backend.api.auth import router as auth_router
from app.backend.api.documents import product_documents_router, router as documents_router
from app.backend.api.products import router as products_router
from app.core.config import PROJECT_ROOT, settings

app = FastAPI(
    title=settings.app_name,
    description="Backend API for AI Sales Support Hub",
    version=settings.app_version,
    openapi_tags=[
        {
            "name": "Health",
            "description": "Health check endpoint",
        }
    ],
)

STATIC_DIR = PROJECT_ROOT / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(products_router, prefix=settings.api_prefix)
app.include_router(documents_router, prefix=settings.api_prefix)
app.include_router(product_documents_router, prefix=settings.api_prefix)
app.include_router(auth_router, prefix=settings.api_prefix)


@app.get("/health", tags=["Health"])
def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": "ai-sales-support-hub-backend",
    }
