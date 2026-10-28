"""Candidate-scoped tools exposed to the product recommendation agent."""

import re
from typing import Any, Callable

from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, Field

from app.ai_agent.services.retrieval import Candidate
from app.ai_agent.services.rule_extraction import STOPWORDS
from app.ai_agent.services.text_safety import neutralize


class RecommendProductsInput(BaseModel):
    query: str = Field(description="Customer requirements for the product recommendation.")
    max_results: int = Field(default=5, ge=1, le=6)


class LookupProductDocumentsInput(BaseModel):
    query: str = Field(description="The named product and document type to find, such as the Dell OptiPlex manual.")


_DOCUMENT_LOOKUP_WORDS = {
    "document", "documents", "manual", "manuals", "publication", "publications",
    "report", "reports", "valuation", "analysis", "datasheet", "datasheets",
    "search", "find", "lookup", "look", "open", "view", "get", "catalog",
}
_LOOKUP_ALIASES = {"labtop": "laptop", "laptpo": "laptop", "notebook": "laptop"}
_LOOKUP_SYNONYMS = {"laptop": {"laptop", "labtop", "laptpo", "notebook", "ultrabook"}}


def select_document_candidate(
    candidates: list[Candidate], query: str
) -> Candidate | None:
    """Choose a product only when at least one meaningful query concept matches it."""
    terms = list(dict.fromkeys(
        _LOOKUP_ALIASES.get(token, token)
        for token in re.findall(r"[a-z0-9]+", query.casefold())
        if token not in _DOCUMENT_LOOKUP_WORDS
        and token not in STOPWORDS
        and len(token) > 1
    ))
    if not terms:
        return None

    ranked: list[tuple[int, Candidate]] = []
    for candidate in candidates:
        product = candidate.product
        identity = " ".join(
            [product.name or "", product.brand or "", product.model or ""]
        ).casefold()
        score = 0
        for term in terms:
            alternatives = _LOOKUP_SYNONYMS.get(term, {term})
            if any(
                re.search(rf"(?<![\w]){re.escape(value)}(?![\w])", identity)
                for value in alternatives
            ):
                score += 1
        ranked.append((score, candidate))
    ranked.sort(
        key=lambda item: (
            0 if item[1].match_type == "exact_match" else 1,
            -item[0],
            -item[1].rrf_score,
            item[1].product.id,
        )
    )
    if not ranked or ranked[0][0] == 0:
        return None
    return ranked[0][1]


class SearchProductDocumentsInput(BaseModel):
    product_id: int = Field(description="ID of a product returned by recommend_products.")
    query: str = Field(
        description="Evidence to look for, such as manual, publication, price, or analysis."
    )
    max_results: int = Field(default=5, ge=1, le=10)


def build_product_tools(
    candidates: list[Candidate],
) -> tuple[list[dict[str, Any]], dict[str, Callable[..., Any]]]:
    """Create tool schemas and handlers restricted to this request's retrieved candidates."""
    by_id = {candidate.product.id: candidate for candidate in candidates}

    def recommend_products(query: str, max_results: int = 5) -> list[dict[str, Any]]:
        terms = _terms(query)
        ranked: list[tuple[int, Candidate]] = []
        for candidate in candidates:
            product = candidate.product
            searchable = " ".join(
                [
                    product.name,
                    product.brand or "",
                    product.category or "",
                    product.type or "",
                    product.sub_type or "",
                    product.description or "",
                    product.key_benefits or "",
                    *product.tags,
                    *product.features,
                    *product.use_cases,
                    *(f"{item['key']} {item['value']}" for item in product.specifications),
                ]
            ).casefold()
            score = sum(term in searchable for term in terms)
            ranked.append((score, candidate))
        ranked.sort(
            key=lambda item: (
                0 if item[1].match_type == "exact_match" else 1,
                -item[0],
                -item[1].rrf_score,
                item[1].product.id,
            )
        )
        return [
            _product_payload(candidate)
            for _, candidate in ranked[:max_results]
        ]

    def lookup_product_documents(query: str) -> dict[str, Any]:
        candidate = select_document_candidate(candidates, query)
        if candidate is None:
            return {"product_id": None, "product": None, "documents": []}
        documents = [
            _document_payload(evidence) for evidence in candidate.documents
        ]
        return {
            "product_id": candidate.product.id,
            "product": _product_payload(candidate),
            "documents": documents,
        }

    def search_product_documents(
        product_id: int, query: str, max_results: int = 5
    ) -> list[dict[str, Any]]:
        candidate = by_id.get(product_id)
        if candidate is None:
            return []
        terms = _terms(query)
        ranked: list[tuple[int, Any]] = []
        for evidence in candidate.documents:
            document = evidence.document
            searchable = " ".join(
                [
                    document.title,
                    document.document_type,
                    document.description or "",
                    document.source_name or "",
                    evidence.excerpt,
                ]
            ).casefold()
            score = sum(term in searchable for term in terms)
            ranked.append((score, evidence))
        ranked.sort(
            key=lambda item: (
                -item[0],
                item[1].document.is_mock,
                item[1].document.document_type,
                item[1].document.id,
            )
        )
        return [
            _document_payload(evidence)
            for score, evidence in ranked[:max_results]
            if score > 0 or not terms
        ]

    schemas = [
        _named_tool_schema(
            LookupProductDocumentsInput,
            "lookup_product_documents",
            "Document-lookup mode. Select one best-matching product and return only that product's linked documents. "
            "Use this when the user asks to find a manual, publication, valuation, or report for a product. "
            "Do not use it for product recommendations.",
        ),
        _named_tool_schema(
            RecommendProductsInput,
            "recommend_products",
            "Recommendation mode. Search and rank products from the candidate set, including relevant alternatives "
            "from other brands, up to max_results. Use this when the user asks for product recommendations.",
        ),
        _named_tool_schema(
            SearchProductDocumentsInput,
            "search_product_documents",
            "Recommendation-support tool. Search documents for a recommended candidate product. "
            "Use only after calling recommend_products; do not use for document-lookup requests.",
        ),
    ]
    return schemas, {
        "lookup_product_documents": lookup_product_documents,
        "recommend_products": recommend_products,
        "search_product_documents": search_product_documents,
    }


def _terms(query: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[\w-]+", query.casefold())))


def _named_tool_schema(
    schema: type[BaseModel], name: str, description: str
) -> dict[str, Any]:
    tool_schema = convert_to_openai_tool(schema)
    function = tool_schema.get("function", tool_schema)
    function["name"] = name
    function["description"] = description
    return tool_schema


def _product_payload(candidate: Candidate) -> dict[str, Any]:
    product = candidate.product
    return {
        "product_id": product.id,
        "name": neutralize(product.name),
        "brand": neutralize(product.brand or ""),
        "category": neutralize(product.category or ""),
        "type": neutralize(product.type or ""),
        "description": neutralize((product.description or "")[:300]),
        "selling_price": str(product.selling_price),
        "currency": product.currency,
        "in_stock": product.stock_quantity > 0 and not product.discontinued,
        "specifications": product.specifications,
        "features": product.features[:8],
        "use_cases": product.use_cases[:6],
        "backend_check": {
            "match_type": candidate.match_type,
            "satisfied": candidate.satisfied,
            "unmet": candidate.unmet,
            "unverified": candidate.unverified,
        },
    }


def _document_payload(evidence: Any) -> dict[str, Any]:
    document = evidence.document
    return {
        "document_id": document.id,
        "title": neutralize(document.title),
        "document_type": document.document_type,
        "source_kind": (
            "live web search results; not a direct document or verified report"
            if document.source_name == "Google Search"
            else "external publisher source; verify exact model and revision"
            if document.source_url
            else "internal supporting document"
        ),
        "source_name": neutralize(document.source_name or ""),
        "source_url": document.source_url,
        "approval_status": document.approval_status,
        "is_mock": document.is_mock,
        "excerpt": neutralize(evidence.excerpt),
    }
