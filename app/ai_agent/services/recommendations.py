"""Stage B: evidence-grounded recommendations, validated against the retrieved candidates."""

import json
from dataclasses import dataclass, field

from app.ai_agent.integrations.openrouter import LLMOutputError, StructuredLLM
from app.ai_agent.prompts.product_suggestion import RECOMMENDATION_PROMPT
from app.ai_agent.schemas.llm_outputs import RecommendationOutput
from app.ai_agent.schemas.product_suggestion import (
    ParsedRequirements,
    ProductRecommendation,
    SupportingDocument,
)
from app.ai_agent.services.retrieval import Candidate
from app.ai_agent.services.text_safety import neutralize
from app.core.config import settings


RECOMMENDATION_MAX_TOKENS = 4000


@dataclass
class ValidatedRecommendations:
    recommendations: list[ProductRecommendation]
    limitations: list[str] = field(default_factory=list)
    summary: str = ""
    intended_use: str | None = None
    required_features: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    clarification_questions: list[str] = field(default_factory=list)


def in_stock(candidate: Candidate) -> bool:
    return candidate.product.stock_quantity > 0 and not candidate.product.discontinued


def candidate_payload(candidate: Candidate) -> dict:
    """Fields shown to the LLM. cost_price, SKU, image URLs and timestamps are deliberately excluded."""
    p = candidate.product
    return {
        "product_id": p.id,
        "name": p.name,
        "brand": p.brand,
        "category": p.category,
        "type": p.type,
        "description": (p.description or "")[:300],
        "selling_price": str(p.selling_price),
        "currency": p.currency,
        "in_stock": in_stock(candidate),
        "key_benefits": p.key_benefits,
        "limitations": p.limitations,
        "specifications": p.specifications,
        "features": p.features[:8],
        "use_cases": p.use_cases[:6],
        "backend_check": {
            "match_type": candidate.match_type,
            "satisfied": candidate.satisfied,
            "unmet": candidate.unmet,
            "unverified": candidate.unverified,
        },
        "retrieval": {
            "keyword_rank": candidate.keyword_rank,
            "semantic_rank": candidate.semantic_rank,
            "semantic_score": candidate.semantic_score,
            "rrf_score": candidate.rrf_score,
        },
        "documents": [
            {
                "document_id": e.document.id,
                "title": e.document.title,
                "document_type": e.document.document_type,
                "is_mock": e.document.is_mock,
                "excerpt": neutralize(e.excerpt),
            }
            for e in candidate.documents
        ],
    }


def _match_type(candidate: Candidate, extra_unmet: list[str]) -> str:

    if candidate.match_type == "exact_match" and extra_unmet:
        return "insufficient_data"
    return candidate.match_type


def build_recommendation(
    candidate: Candidate,
    reasons: list[str],
    extra_unmet: list[str],
    document_ids: list[str],
    follow_up_question: str | None = None,
) -> ProductRecommendation:
    """Every product fact comes from the database record, never from model output."""
    p = candidate.product
    provided = {e.document.id: e.document for e in candidate.documents}
    return ProductRecommendation(
        product_id=p.id,
        name=p.name,
        brand=p.brand,
        sku=p.sku,
        category=p.category,
        selling_price=p.selling_price,
        currency=p.currency,
        in_stock=in_stock(candidate),
        stock_quantity=p.stock_quantity,
        match_type=_match_type(candidate, extra_unmet),
        reasons=reasons,
        satisfied_requirements=candidate.satisfied,
        unmet_requirements=list(dict.fromkeys([*candidate.unmet, *extra_unmet])),
        unverified_requirements=candidate.unverified,
        follow_up_question=follow_up_question,
        supporting_documents=[
            SupportingDocument(
                document_id=provided[i].id,
                title=provided[i].title,
                document_type=provided[i].document_type,
                is_mock=provided[i].is_mock,
                approval_status=provided[i].approval_status,
                file_url=f"{settings.api_prefix}/documents/{provided[i].id}/file",
            )
            for i in document_ids
        ],
    )


def fallback_recommendations(
    candidates: list[Candidate], max_products: int
) -> list[ProductRecommendation]:
    """Used when the model is unavailable: deterministic, using the same classification as the AI path."""
    return [
        build_recommendation(candidate, [_fallback_reason(candidate)], [], [])
        for candidate in candidates[:max_products]
    ]


def _fallback_reason(candidate: Candidate) -> str:
    prefix = {
        "exact_match": "Meets every requirement the backend could check.",
        "insufficient_data": "Nothing checked failed, but some requirements could not be verified.",
        "alternative": "Relevant alternative that does not meet every requirement.",
    }[candidate.match_type]
    return f"{prefix} Selected by catalog data and rules, not AI ranking."


def validate_output(
    output: RecommendationOutput, candidates: list[Candidate], max_products: int
) -> ValidatedRecommendations:
    by_id = {c.product.id: c for c in candidates}
    recommendations: list[ProductRecommendation] = []
    seen: set[int] = set()
    discarded = False

    for item in output.recommendations:
        candidate = by_id.get(item.product_id)
        if candidate is None or item.product_id in seen or not item.reasons:
            discarded = True
            continue
        if len(recommendations) >= max_products:
            break
        seen.add(item.product_id)
        provided = {e.document.id: e.document for e in candidate.documents}
        cited = list(
            dict.fromkeys(i for i in item.supporting_document_ids if i in provided)
        )
        if len(cited) != len(set(item.supporting_document_ids)):
            discarded = True
        recommendations.append(
            build_recommendation(
                candidate,
                item.reasons,
                item.unmet_requirements,
                cited,
                item.follow_up_question,
            )
        )

    if output.recommendations and not recommendations:
        raise LLMOutputError("Model referenced no valid candidate products")

    tier = {"exact_match": 0, "insufficient_data": 1, "alternative": 2}
    recommendations.sort(key=lambda r: tier[r.match_type])
    recommendations.sort(key=lambda r: tier[r.match_type])
    recommendations = recommendations[:max_products]

    limitations = list(output.limitations)
    if discarded:
        limitations.append(
            "Some model-suggested items referenced unknown products or documents and were discarded."
        )
    return ValidatedRecommendations(
        recommendations,
        limitations,
        output.summary,
        output.intended_use,
        output.required_features,
        output.missing_information,
        output.clarification_questions,
    )


async def generate_recommendations(
    llm: StructuredLLM,
    *,
    requirements: str,
    parsed: ParsedRequirements,
    candidates: list[Candidate],
    max_products: int,
) -> ValidatedRecommendations:
    output = await llm.ainvoke_structured(
        RECOMMENDATION_PROMPT,
        RecommendationOutput,
        {
            "requirements": neutralize(requirements),
            "parsed": json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False),
            "candidates": json.dumps(
                [candidate_payload(c) for c in candidates], ensure_ascii=False
            ),
            "max_products": max_products,
        },
        max_tokens=RECOMMENDATION_MAX_TOKENS,
    )
    return validate_output(output, candidates, max_products)
