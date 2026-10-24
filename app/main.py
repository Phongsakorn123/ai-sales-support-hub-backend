from fastapi import FastAPI

from app.backend.api.auth import router as auth_router
from app.backend.api.products import router as products_router
from app.core.config import settings

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

app.include_router(products_router, prefix=settings.api_prefix)
app.include_router(auth_router, prefix=settings.api_prefix)


@app.get("/health", tags=["Health"])
def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": "ai-sales-support-hub-backend",
    }
