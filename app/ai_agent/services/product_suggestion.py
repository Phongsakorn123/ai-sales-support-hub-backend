"""Orchestrates the workflow: rule-based requirement parsing and retrieval, then ONE model call to rank."""

import logging

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.ai_agent.integrations.openrouter import (
    LLMError,
    LLMNotConfiguredError,
    StructuredLLM,
)
from app.ai_agent.schemas.product_suggestion import (
    ProductSuggestionRequest,
    ProductSuggestionResponse,
)
from app.ai_agent.services.recommendations import (
    ValidatedRecommendations,
    fallback_recommendations,
    generate_recommendations,
)
from app.ai_agent.services.requirements import build_criteria
from app.ai_agent.services.retrieval import Candidate, retrieve_candidates
from app.ai_agent.services.request_intent import is_document_lookup_request
from app.backend.services import products as product_service

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "AI-generated suggestions for sales staff. Prices, stock and product data come from the catalog. "
    "Supporting documents marked is_mock are demo data, not verified manufacturer documentation. "
    "Confirm details before quoting a customer."
)
MOCK_NOTE = "Cited supporting documents are mock demo data and are not verified manufacturer documentation."
REFERENCE_NOTE = "Some external links open live search results rather than a specific document. Treat search results as leads, not verified reports or valuations; confirm exact SKU, region, date, revision and current terms before sharing or quoting."
FALLBACK_NOTE = (
    "The AI model could not be reached, so products were not ranked or assessed by AI. They match the "
    "backend filters only; verify other requirements manually."
)
NO_EXACT_NOTE = (
    "No product fully meets all of the stated requirements. The products below are alternatives; each one "
    "lists what it does not meet or what could not be verified."
)
PARTIAL_EXACT_NOTE = "Fewer products than requested fully meet the requirements; the rest are labelled alternatives."
NO_AI_SELECTION_NOTE = (
    "The AI model did not select any product, so the closest alternatives were chosen by catalog rules "
    "and are not AI-ranked."
)
NO_DOCS_NOTE = (
    "No supporting document evidence was found for some recommended products."
)


def _catalog_fallback_result(
    candidates: list[Candidate], max_products: int, summary: str
) -> ValidatedRecommendations:
    return ValidatedRecommendations(
        fallback_recommendations(candidates, max_products),
        [FALLBACK_NOTE],
        summary,
    )


async def suggest_products(
    db: Session, llm: StructuredLLM | None, request: ProductSuggestionRequest
) -> ProductSuggestionResponse:
    taxonomy = await run_in_threadpool(product_service.list_catalog_taxonomy, db)
    criteria = build_criteria(request.requirements, taxonomy)

    retrieval = await run_in_threadpool(
        retrieve_candidates,
        db,
        criteria,
        include_out_of_stock=request.include_out_of_stock,
        max_products=request.max_products,
    )
    limitations = [*criteria.notes, *retrieval.notes]
    parsed = criteria.parsed

    def catalog_fallback(summary: str) -> ValidatedRecommendations:
        # Intent heuristics are only a degraded fallback; the configured LLM chooses
        # the mode tool during normal agent execution.
        if is_document_lookup_request(request.requirements, criteria):
            from app.ai_agent.tools.product_tools import select_document_candidate

            candidate = select_document_candidate(
                retrieval.candidates, request.requirements
            )
            if candidate is None:
                return ValidatedRecommendations(
                    [],
                    [FALLBACK_NOTE],
                    "No matching catalog product was found for this document request.",
                )
            return _catalog_fallback_result(
                [candidate], 1, summary
            )
        return _catalog_fallback_result(
            retrieval.candidates, request.max_products, summary
        )

    if not retrieval.candidates:
        explanation = "No suitable product or meaningful alternative was found in the available catalog for these requirements."
        if parsed.missing_information:
            explanation += " More information from the customer may help."
        return ProductSuggestionResponse(
            parsed_requirements=parsed,
            recommendations=[],
            summary=explanation,
            limitations=limitations,
            data_sufficiency="insufficient",
            candidates_considered=0,
            model=llm.model_name if llm else "OpenRouter not configured",
            retrieval_mode=retrieval.retrieval_mode,
            disclaimer=DISCLAIMER,
        )

    ranking_source = "ai"
    if llm is None:
        logger.info("OpenRouter is not configured; returning catalog filter matches")
        ranking_source = "catalog_fallback"
        result = catalog_fallback(
            "OpenRouter is not configured. These products were selected and ordered by catalog data and rules, not AI ranking.",
        )
    else:
        try:
            result = await generate_recommendations(
                llm,
                requirements=request.requirements,
                parsed=parsed,
                candidates=retrieval.candidates,
                max_products=request.max_products,
            )
        except LLMError as error:
            if isinstance(error, LLMNotConfiguredError):
                raise
            logger.warning(
                "Model unavailable (%s); returning catalog filter matches",
                type(error).__name__,
            )
            ranking_source = "catalog_fallback"
            result = catalog_fallback(
                "The AI model was unavailable. These products were selected and ordered by catalog data and rules, not AI ranking.",
            )
    if (
        ranking_source == "ai"
        and not result.recommendations
        and not any(c.match_type == "exact_match" for c in retrieval.candidates)
    ):

        ranking_source = "catalog_fallback"
        fallback_candidates = retrieval.candidates
        fallback_limit = request.max_products
        if is_document_lookup_request(request.requirements, criteria):
            from app.ai_agent.tools.product_tools import select_document_candidate

            candidate = select_document_candidate(
                fallback_candidates, request.requirements
            )
            fallback_candidates = [candidate] if candidate else []
            fallback_limit = 1
        result = ValidatedRecommendations(
            fallback_recommendations(fallback_candidates, fallback_limit),
            [*result.limitations, NO_AI_SELECTION_NOTE],
            result.summary
            or "No product fully meets the requirements; closest alternatives chosen by catalog rules.",
            result.intended_use,
            result.required_features,
            result.missing_information,
            result.clarification_questions,
        )
    parsed.intended_use = result.intended_use
    parsed.required_features = list(
        dict.fromkeys([*parsed.required_features, *result.required_features])
    )
    parsed.missing_information = result.missing_information
    parsed.clarification_questions = result.clarification_questions
    limitations.extend(result.limitations)
    recommendations = result.recommendations

    documents = [d for r in recommendations for d in r.supporting_documents]
    if any(d.is_mock for d in documents):
        limitations.append(MOCK_NOTE)
    if any(d.source_url for d in documents):
        limitations.append(REFERENCE_NOTE)
    if recommendations and any(not r.supporting_documents for r in recommendations):
        limitations.append(NO_DOCS_NOTE)

    if not recommendations:
        sufficiency = "insufficient"
    elif (
        parsed.missing_information
        or any(
            r.unmet_requirements
            or r.unverified_requirements
            or not r.supporting_documents
            for r in recommendations
        )
        or any(d.is_mock for d in documents)
    ):
        sufficiency = "partial"
    else:
        sufficiency = "sufficient"

    exact_count = sum(r.match_type == "exact_match" for r in recommendations)
    if recommendations and exact_count == 0:
        limitations.append(NO_EXACT_NOTE)
    elif 0 < exact_count < len(recommendations):
        limitations.append(PARTIAL_EXACT_NOTE)

    summary = result.summary or (
        "The model found no suitable product among the candidates."
        if not recommendations
        else ""
    )
    return ProductSuggestionResponse(
        parsed_requirements=parsed,
        recommendations=recommendations,
        summary=summary,
        limitations=list(dict.fromkeys(limitations)),
        data_sufficiency=sufficiency,
        candidates_considered=len(retrieval.candidates),
        exact_match_count=exact_count,
        model=llm.model_name if llm else "OpenRouter not configured",
        ranking_source=ranking_source,
        retrieval_mode=retrieval.retrieval_mode,
        disclaimer=DISCLAIMER,
    )
