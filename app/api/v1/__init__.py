"""Version 1 API router composition."""

from fastapi import APIRouter

from app.ai_agent.api.product_suggestions import router as ai_router
from app.backend.api.auth import router as auth_router
from app.backend.api.documents import (
    product_documents_router,
    router as documents_router,
)
from app.backend.api.products import router as products_router

router = APIRouter()
router.include_router(products_router)
router.include_router(documents_router)
router.include_router(product_documents_router)
router.include_router(ai_router)
router.include_router(auth_router)
