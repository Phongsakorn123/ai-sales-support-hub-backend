"""Hybrid product retrieval: lexical and local-vector retrieval fused with RRF."""

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from app.ai_agent.services import embeddings
from app.ai_agent.services.requirements import SearchCriteria
from app.ai_agent.services.rule_extraction import STOPWORDS, product_ram_gb
from app.backend.models.document import Document
from app.backend.models.product import Product
from app.backend.services import documents as document_service
from app.backend.services import products as product_service
from app.core.config import settings

logger = logging.getLogger(__name__)

POOL_SIZE = 100
MAX_CATALOG_PRODUCTS = 5000
MAX_CANDIDATES = 6
ALTERNATIVE_BUDGET_CEILING = Decimal("1.5")
EXCERPT_CHARS = 300
DOCUMENTS_PER_PRODUCT = 1
DOCUMENT_PREFERENCE = [
    "datasheet",
    "product_manual",
    "compatibility_guide",
    "brochure",
    "validation_report",
]
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass
class DocumentEvidence:
    document: Document
    excerpt: str


@dataclass
class Candidate:
    """A retrieved product plus database-verifiable requirement checks."""

    product: Product
    match_type: str = "insufficient_data"
    satisfied: list[str] = field(default_factory=list)
    unmet: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    sort_key: tuple = ()
    documents: list[DocumentEvidence] = field(default_factory=list)
    keyword_rank: int | None = None
    semantic_rank: int | None = None
    semantic_score: float | None = None
    rrf_score: float = 0.0


@dataclass
class Retrieval:
    candidates: list[Candidate]
    notes: list[str]
    retrieval_mode: str = "keyword_fallback"


def _terms(criteria: SearchCriteria) -> list[str]:
    parsed = criteria.parsed
    values = [*parsed.required_features, *criteria.keywords]
    if parsed.intended_use:
        values.append(parsed.intended_use)

    values.extend(
        token
        for token in _WORD.findall(criteria.query_text.lower())
        if len(token) >= 2 and token not in STOPWORDS
    )
    return list(dict.fromkeys(value for value in values if value.strip()))


def _haystack(product: Product) -> str:
    parts = [
        product.name,
        product.brand,
        product.model,
        product.description,
        product.category,
        product.type,
        product.sub_type,
        product.key_benefits,
        *product.tags,
        *product.features,
        *product.use_cases,
        *(f"{item['key']} {item['value']}" for item in product.specifications),
    ]
    return " ".join(part for part in parts if part).lower()


def _score(text: str, terms: list[str]) -> float:
    score = 0.0
    for term in terms:
        tokens = [token for token in _WORD.findall(term.lower()) if len(token) >= 2]
        if tokens:
            score += sum(1 for token in tokens if token in text) / len(tokens)
    return score


def _category_match(product: Product, criteria: SearchCriteria) -> bool:
    if not criteria.category_terms:
        return True
    wanted = {term.casefold() for term in criteria.category_terms}
    return any(
        value and value.casefold() in wanted
        for value in (product.category, product.type, product.sub_type)
    )


def _excerpt(text: str, terms: list[str]) -> str:
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.startswith("## ")), 0
    )
    body = [
        line.strip()
        for line in lines[start:]
        if line.strip() and not line.startswith("|---")
    ]
    hits = [line for line in body if _score(line.lower(), terms) > 0]
    return " ".join(hits or body)[:EXCERPT_CHARS]


def _load_documents(
    db: Session, product: Product, terms: list[str], notes: list[str]
) -> list[DocumentEvidence]:
    evidence: list[tuple[float, int, DocumentEvidence]] = []
    for document in document_service.list_product_documents(
        db, product.id, limit=20, offset=0
    ):
        try:
            text = document_service.read_document_text(document)
        except document_service.DocumentFileError:
            note = "Some supporting document files could not be read and were not used."
            if note not in notes:
                notes.append(note)
            continue
        order = (
            DOCUMENT_PREFERENCE.index(document.document_type)
            if document.document_type in DOCUMENT_PREFERENCE
            else 99
        )
        evidence.append(
            (
                _score(text.lower(), terms),
                order,
                DocumentEvidence(document, _excerpt(text, terms)),
            )
        )
    evidence.sort(key=lambda item: (-item[0], item[1], item[2].document.id))
    return [item[2] for item in evidence[:DOCUMENTS_PER_PRODUCT]]


def _money(value: Decimal, currency: str) -> str:
    return f"{value:,.0f} {currency}"


def evaluate_product(
    product: Product,
    criteria: SearchCriteria,
    terms: list[str],
    *,
    category_matched: bool,
) -> Candidate:
    """Check hard constraints using authoritative catalog fields only."""
    parsed = criteria.parsed
    satisfied: list[str] = []
    unmet: list[str] = []
    unverified: list[str] = []
    over_ratio = 0.0
    ram_gap = 0.0

    if criteria.category_terms:
        label = " / ".join(criteria.category_terms)
        (satisfied if category_matched else unmet).append(
            f"Category: {label}"
            if category_matched
            else f"Not in the requested category ({label})"
        )
    if criteria.brand_filter:
        matches = (product.brand or "").lower() == criteria.brand_filter.lower()
        (satisfied if matches else unmet).append(
            f"Preferred brand: {criteria.brand_filter}"
            if matches
            else f"Different brand from the preferred {criteria.brand_filter}"
        )

    if product.stock_quantity > 0 and not product.discontinued:
        satisfied.append("In stock")
    else:
        unmet.append("Currently out of stock or discontinued")

    if parsed.budget_max is not None:
        price, budget = product.selling_price, parsed.budget_max
        if parsed.currency and product.currency.upper() != parsed.currency.upper():
            unverified.append(
                f"Price is listed in {product.currency}, so it cannot be compared with the {parsed.currency} budget"
            )
        elif price <= budget:
            satisfied.append(
                f"Within budget ({_money(price, product.currency)} of {_money(budget, product.currency)})"
            )
        else:
            over_ratio = float((price - budget) / budget)
            unmet.append(f"Over budget by {_money(price - budget, product.currency)}")

    if criteria.min_ram_gb:
        ram = product_ram_gb(product)
        if ram is None:
            unverified.append(
                f"RAM of at least {criteria.min_ram_gb:g}GB could not be confirmed from the catalog"
            )
        elif ram >= criteria.min_ram_gb:
            satisfied.append(
                f"RAM {ram:g}GB meets the requested {criteria.min_ram_gb:g}GB minimum"
            )
        else:
            ram_gap = criteria.min_ram_gb - ram
            unmet.append(
                f"RAM {ram:g}GB is below the requested {criteria.min_ram_gb:g}GB"
            )

    match_type = (
        "alternative" if unmet else "insufficient_data" if unverified else "exact_match"
    )
    tier = {"exact_match": 0, "insufficient_data": 1, "alternative": 2}[match_type]
    return Candidate(
        product,
        match_type,
        satisfied,
        unmet,
        unverified,
        (tier, len(unmet), over_ratio, ram_gap, product.id),
    )


def reciprocal_rank_fusion(
    keyword_ids: list[int],
    semantic_ids: list[int],
    *,
    k: int,
    keyword_weight: float,
    semantic_weight: float,
) -> dict[int, dict[str, float | int | None]]:
    """Fuse ranked lists without adding incomparable raw lexical and cosine scores."""
    fused: dict[int, dict[str, float | int | None]] = {}
    for rank, product_id in enumerate(keyword_ids, start=1):
        entry = fused.setdefault(
            product_id,
            {
                "keyword_rank": None,
                "semantic_rank": None,
                "semantic_score": None,
                "rrf_score": 0.0,
            },
        )
        entry["keyword_rank"] = rank
        entry["rrf_score"] = float(entry["rrf_score"]) + keyword_weight / (k + rank)
    for rank, (product_id, cosine_score) in enumerate(semantic_ids, start=1):
        entry = fused.setdefault(
            product_id,
            {
                "keyword_rank": None,
                "semantic_rank": None,
                "semantic_score": None,
                "rrf_score": 0.0,
            },
        )
        entry["semantic_rank"] = rank
        entry["semantic_score"] = cosine_score
        entry["rrf_score"] = float(entry["rrf_score"]) + semantic_weight / (k + rank)
    return fused


def retrieve_candidates(
    db: Session,
    criteria: SearchCriteria,
    *,
    include_out_of_stock: bool,
    max_products: int,
) -> Retrieval:
    notes: list[str] = []
    terms = _terms(criteria)
    parsed = criteria.parsed
    if not criteria.category_terms and not terms:
        return Retrieval(
            [],
            [
                "The request is too general to search the catalog; more detail is needed."
            ],
        )

    catalog = product_service.search_products(
        db, in_stock_only=not include_out_of_stock, limit=MAX_CATALOG_PRODUCTS
    )
    category_products = [
        product for product in catalog if _category_match(product, criteria)
    ]
    if criteria.category_terms and category_products:

        products = category_products
        category_matched = True
    else:
        products = catalog
        category_matched = not criteria.category_terms
        if criteria.category_terms:
            notes.append(
                "No product matched the requested category; keyword and semantic search used the full catalog."
            )
    by_id = {product.id: product for product in products}
    keyword_hits = [
        product
        for product in products
        if _score(_haystack(product), terms) > 0
        or (bool(criteria.category_terms) and _category_match(product, criteria))
    ]
    keyword_hits.sort(
        key=lambda product: (
            -(
                _score(_haystack(product), terms)
                + (
                    1.0
                    if criteria.category_terms and _category_match(product, criteria)
                    else 0.0
                )
            ),
            product.id,
        )
    )
    keyword_hits = keyword_hits[:POOL_SIZE]
    keyword_ids = [product.id for product in keyword_hits]

    semantic_ids: list[tuple[int, float]] = []
    usable_embeddings = 0
    semantic_failed = False
    try:
        if terms:
            semantic_ids, usable_embeddings = embeddings.semantic_search(
                db,
                criteria.query_text or " ".join(terms),
                limit=POOL_SIZE,
                eligible_ids=set(by_id),
            )
    except Exception as error:
        semantic_failed = True
        logger.warning(
            "Semantic retrieval unavailable (error=%s)", type(error).__name__
        )
        notes.append(
            "Semantic search is unavailable; results use keyword retrieval only."
        )

    if not semantic_failed and usable_embeddings:
        mode = "hybrid"
        if usable_embeddings < len(products):
            notes.append(
                "The semantic index is incomplete; some products were searched by keywords only."
            )
    else:
        mode = "keyword_fallback"
        if not semantic_failed:
            notes.append(
                "No product embeddings are indexed yet; results use keyword retrieval only."
            )

    fused = reciprocal_rank_fusion(
        keyword_ids,
        semantic_ids,
        k=settings.retrieval_rrf_k,
        keyword_weight=settings.retrieval_keyword_weight,
        semantic_weight=settings.retrieval_semantic_weight,
    )
    ranked_ids = sorted(
        fused,
        key=lambda product_id: (-float(fused[product_id]["rrf_score"]), product_id),
    )
    evaluated: list[Candidate] = []
    for product_id in ranked_ids:
        product = by_id.get(product_id)
        if product is None:
            continue
        candidate = evaluate_product(
            product, criteria, terms, category_matched=category_matched
        )
        metadata = fused[product_id]
        candidate.keyword_rank = metadata["keyword_rank"]  # type: ignore[assignment]
        candidate.semantic_rank = metadata["semantic_rank"]  # type: ignore[assignment]
        candidate.semantic_score = metadata["semantic_score"]  # type: ignore[assignment]
        candidate.rrf_score = float(metadata["rrf_score"])
        evaluated.append(candidate)

    if parsed.budget_max is not None:

        ceiling = parsed.budget_max * ALTERNATIVE_BUDGET_CEILING
        kept = [
            candidate
            for candidate in evaluated
            if candidate.product.currency.upper()
            != (parsed.currency or candidate.product.currency).upper()
            or candidate.product.selling_price <= ceiling
        ]
        if len(kept) < len(evaluated):
            notes.append(
                f"{len(evaluated) - len(kept)} relevant product(s) were left out because they are far above the stated budget."
            )
        evaluated = kept

    if not evaluated:
        if not include_out_of_stock:
            notes.append(
                "Out-of-stock and discontinued products were excluded from the search."
            )
        notes.append(
            "No suitable product or meaningful alternative was found in the available catalog."
        )
        return Retrieval([], notes, mode)

    evaluated.sort(
        key=lambda candidate: (
            *candidate.sort_key[:4],
            -candidate.rrf_score,
            candidate.product.id,
        )
    )
    candidates = evaluated[: min(max_products + 2, MAX_CANDIDATES)]
    for candidate in candidates:
        candidate.documents = _load_documents(db, candidate.product, terms, notes)
    return Retrieval(candidates, notes, mode)
