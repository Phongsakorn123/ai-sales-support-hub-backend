import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.ai_agent.integrations.openrouter import (
    LLMNotConfiguredError,
    LLMOutputError,
    LLMRateLimitedError,
    LLMTimeoutError,
    LLMUpstreamError,
    OpenRouterClient,
    StructuredLLM,
)
from app.ai_agent.schemas.product_suggestion import (
    ProductSuggestionRequest,
    ProductSuggestionResponse,
)
from app.ai_agent.services.product_suggestion import suggest_products
from app.backend.dependencies.auth import get_current_user
from app.backend.services.documents import DocumentPersistenceError
from app.backend.services.products import ProductPersistenceError
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ai",
    tags=["AI Agent"],
    dependencies=[Depends(get_current_user)],
)


def get_llm() -> StructuredLLM:
    try:
        return OpenRouterClient.from_settings()
    except LLMNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The AI service is not configured.",
        ) from None


@router.post(
    "/product-suggestions",
    response_model=ProductSuggestionResponse,
    summary="Suggest products for a customer's requirements",
    description=(
        "Parses budget, currency, category, brand and minimum RAM with deterministic rules, retrieves "
        "candidates through keyword and local multilingual semantic search fused with Reciprocal Rank "
        "Fusion (or explicitly reported keyword-only fallback), includes linked Markdown documents, then makes ONE call to the configured "
        "OpenRouter model to rank and explain them. Names, prices, stock and document metadata always "
        "come from the database. If the model is unavailable, catalog filter matches are returned with "
        "`ranking_source=catalog_fallback`. Seeded documents are mock demo data, not verified "
        "manufacturer evidence."
    ),
    responses={
        502: {
            "description": "The AI service failed, timed out or returned unusable output"
        },
        503: {"description": "The AI service is not configured"},
        500: {"description": "Database operation failed"},
    },
)
async def create_product_suggestions(
    request: ProductSuggestionRequest,
    db: Session = Depends(get_db),
    llm: StructuredLLM = Depends(get_llm),
) -> ProductSuggestionResponse:
    try:
        return await suggest_products(db, llm, request)
    except LLMTimeoutError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "The AI service timed out."
        ) from None
    except LLMOutputError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "The AI service returned an unusable response."
        ) from None
    except LLMRateLimitedError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "The AI service is rate-limited right now. Please try again shortly.",
        ) from None
    except LLMUpstreamError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "The AI service is unavailable or returned an error.",
        ) from None
    except (ProductPersistenceError, DocumentPersistenceError) as error:
        logger.error(
            "Product suggestion database error",
            exc_info=(type(error), error, error.__traceback__),
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "A database error occurred while processing the suggestion request.",
        ) from None
