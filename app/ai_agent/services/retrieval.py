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
DOCUMENTS_PER_PRODUCT = 2
DOCUMENT_PREFERENCE = [
    "datasheet",
    "valuation_report",
    "publication",
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


_QUERY_INTENT_WORDS = {
    "search", "find", "look", "looking", "need", "want", "show",
    "recommend", "recommendation", "suggest", "suggestion", "related", "help",
    "product", "products", "document", "documents", "manual", "manuals",
    "publication", "publications", "report", "reports", "analysis",
    "valuation", "price", "information", "info", "please",
}
_QUERY_ALIASES = {
    "labtop": "laptop",
    "laptpo": "laptop",
    "notebok": "notebook",
    "notebook": "laptop",
    "ultrabook": "laptop",
    "display": "monitor",
    "screen": "monitor",
    "headset": "headphones",
    "earphones": "headphones",
    "mice": "mouse",
}
_QUERY_SYNONYMS = {
    "laptop": {
        "laptop", "labtop", "laptpo", "notebook", "notebok", "ultrabook",
    },
    "monitor": {"monitor", "display", "screen"},
    "headphones": {"headphones", "headset", "earphones"},
    "mouse": {"mouse", "mice"},
    "keyboard": {"keyboard", "keyboards"},
}
_GENERIC_PRODUCT_TYPES = {
    "laptop", "desktop", "computer", "workstation", "server", "monitor",
    "headphones", "keyboard", "mouse", "router", "switch",
}


def _query_concepts(criteria: SearchCriteria) -> list[str]:
    concepts = []
    for token in re.findall(r"[a-z0-9]+", criteria.query_text.casefold()):
        if token in _QUERY_INTENT_WORDS or token in STOPWORDS or len(token) < 2:
            continue
        concepts.append(_QUERY_ALIASES.get(token, token))
    return list(dict.fromkeys(concepts))


def _concept_matches(term: str, text: str) -> bool:
    alternatives = _QUERY_SYNONYMS.get(term, {term})
    return any(
        re.search(rf"(?<![\w]){re.escape(value)}(?![\w])", text)
        for value in alternatives
    )


def _exact_keyword_matches(
    products: list[Product], concepts: list[str]
) -> list[Product]:
    """Return products matching every query concept (including known synonyms)."""
    if not concepts:
        return []
    return [
        product
        for product in products
        if all(_concept_matches(concept, _haystack(product)) for concept in concepts)
    ]


def _identity_similar_matches(
    products: list[Product], criteria: SearchCriteria, concepts: list[str]
) -> list[Product] | None:
    """Keep explicit brand/model identity in scope before broader semantic matching."""
    if criteria.brand_filter:
        scoped = [
            product for product in products
            if (product.brand or "").casefold() == criteria.brand_filter.casefold()
        ]
    else:
        scoped = products

    brand_tokens = set(re.findall(
        r"[a-z0-9]+", (criteria.brand_filter or "").casefold()
    ))
    identity_text = " ".join(
        f"{product.name or ''} {product.model or ''}" for product in scoped
    ).casefold()
    family_terms = [
        term for term in concepts
        if term not in _GENERIC_PRODUCT_TYPES
        and term not in brand_tokens
        and any(
            _concept_matches(term, f"{product.name or ''} {product.model or ''}".casefold())
            for product in scoped
        )
    ]
    if family_terms:
        return [
            product for product in scoped
            if all(
                _concept_matches(term, f"{product.name or ''} {product.model or ''}".casefold())
                for term in family_terms
            )
        ]

    if criteria.brand_filter and scoped:
        category_matches = [
            product for product in scoped if _category_match(product, criteria)
        ]
        return category_matches or scoped
    return None


def _recommendation_candidate_pool(
    catalog: list[Product],
    anchor_products: list[Product],
    criteria: SearchCriteria,
) -> list[Product]:
    """Add same-category products so recommendations can include useful alternatives."""
    if criteria.category_terms:
        related = [p for p in catalog if _category_match(p, criteria)]
    else:
        anchor_types = {
            p.type.casefold() for p in anchor_products if p.type
        }
        related = [
            p for p in catalog
            if p.type and p.type.casefold() in anchor_types
        ]
    if related:
        return list({p.id: p for p in [*anchor_products, *related]}.values())
    return anchor_products or catalog


def _keyword_relevance(product: Product, concepts: list[str]) -> tuple[float, float]:
    """Score partial matches, giving exact model/family terms more value than broad types."""
    text = _haystack(product)
    identity = f"{product.name or ''} {product.model or ''}".casefold()
    matched_weight = 0.0
    total_weight = 0.0
    matched_count = 0
    for concept in concepts:
        weight = 1.0 if concept in _GENERIC_PRODUCT_TYPES else 2.0
        if _concept_matches(concept, identity) and concept not in _GENERIC_PRODUCT_TYPES:
            weight += 2.0
        total_weight += weight
        if _concept_matches(concept, text):
            matched_weight += weight
            matched_count += 1
    return (
        matched_weight / total_weight if total_weight else 0.0,
        matched_count / len(concepts) if concepts else 0.0,
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
    evidence: list[tuple[int, float, int, DocumentEvidence]] = []
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
        category_order = (
            0 if document.document_type in {"datasheet", "publication", "product_manual"}
            else 1 if document.document_type == "valuation_report"
            else 2
        )
        evidence.append(
            (
                category_order,
                _score(text.lower(), terms),
                order,
                DocumentEvidence(document, _excerpt(text, terms)),
            )
        )
    evidence.sort(key=lambda item: (item[3].document.is_mock, item[0], -item[1], item[2], item[3].document.id))
    return [item[3] for item in evidence[:DOCUMENTS_PER_PRODUCT]]


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
    single_product_lookup: bool = False,
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

    products = product_service.search_products(
        db, in_stock_only=not include_out_of_stock, limit=MAX_CATALOG_PRODUCTS
    )
    query_concepts = _query_concepts(criteria)
    exact_products = _exact_keyword_matches(products, query_concepts)
    exact_match_ids = {product.id for product in exact_products}
    identity_matches = (
        _identity_similar_matches(products, criteria, query_concepts)
        if not exact_products
        else None
    )
    identity_match_ids = {product.id for product in identity_matches or []}
    if single_product_lookup and exact_products:
        # Document lookups must not return substitute products.
        products = exact_products
        notes.append(
            "Exact keyword and synonym matches were found for the document lookup."
        )
    elif single_product_lookup:
        if identity_matches:
            products = identity_matches
            notes.append(
                "No exact product matched every query concept; document lookup is scoped to the closest explicit brand/model matches."
            )
        elif criteria.category_terms and any(
            _category_match(product, criteria) for product in products
        ):
            products = [p for p in products if _category_match(p, criteria)]
            notes.append(
                "No exact product matched every query concept; document lookup is scoped to the requested product category."
            )
        else:
            notes.append(
                "No exact product or explicit brand/model match was found; selecting the closest hybrid match for document lookup."
            )
    else:
        anchors = exact_products or identity_matches or []
        if not anchors and criteria.category_terms:
            anchors = [p for p in products if _category_match(p, criteria)]
        products = _recommendation_candidate_pool(products, anchors, criteria)
        if not exact_products:
            notes.append(
                "No exact product matched every query concept; showing closest same-category and cross-brand alternatives."
            )
    by_id = {product.id: product for product in products}
    keyword_hits = [
        product
        for product in products
        if _keyword_relevance(product, query_concepts)[0] > 0
        or (not query_concepts and criteria.category_terms and _category_match(product, criteria))
    ]
    keyword_hits.sort(
        key=lambda product: (
            -_keyword_relevance(product, query_concepts)[0],
            -_keyword_relevance(product, query_concepts)[1],
            product.id,
        )
    )
    keyword_hits = keyword_hits[:POOL_SIZE]
    keyword_ids = [product.id for product in keyword_hits]

    semantic_ids: list[tuple[int, float]] = []
    usable_embeddings = 0
    semantic_failed = False
    try:
        if terms or query_concepts:
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
        keyword_weight=1.0,
        semantic_weight=1.0,
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
            product,
            criteria,
            terms,
            category_matched=(
                not criteria.category_terms or _category_match(product, criteria)
            ),
        )
        if product_id not in exact_match_ids:
            candidate.match_type = "alternative"
            candidate.unmet.append(
                "This product did not match all query concepts exactly; it is a similar hybrid-search result."
            )
            candidate.sort_key = (
                2, len(candidate.unmet), *candidate.sort_key[2:]
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

    priority_ids = exact_match_ids or identity_match_ids
    if priority_ids and not single_product_lookup:
        evaluated.sort(
            key=lambda candidate: (
                0 if candidate.product.id in priority_ids else 1,
                -candidate.rrf_score,
                candidate.keyword_rank or POOL_SIZE + 1,
                candidate.semantic_rank or POOL_SIZE + 1,
                candidate.product.id,
            )
        )
    else:
        evaluated.sort(
            key=lambda candidate: (
                *candidate.sort_key[:4],
                -candidate.rrf_score,
                candidate.product.id,
            )
        )
    candidates = evaluated[: min(max_products + 2, MAX_CANDIDATES)]
    if single_product_lookup:
        candidates = candidates[:1]
    for candidate in candidates:
        candidate.documents = _load_documents(db, candidate.product, terms, notes)
    return Retrieval(candidates, notes, mode)
