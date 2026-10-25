"""Candidate product and document retrieval, built on the existing Core Backend services."""

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from app.ai_agent.services.requirements import SearchCriteria
from app.ai_agent.services.rule_extraction import product_ram_gb
from app.backend.models.document import Document
from app.backend.models.product import Product
from app.backend.services import documents as document_service
from app.backend.services import products as product_service

logger = logging.getLogger(__name__)

POOL_SIZE = 100
MAX_CANDIDATES = 6
ALTERNATIVE_BUDGET_CEILING = Decimal("1.5")
EXCERPT_CHARS = 300
DOCUMENTS_PER_PRODUCT = 1
DOCUMENT_PREFERENCE = ["datasheet", "product_manual", "compatibility_guide", "brochure", "validation_report"]
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass
class DocumentEvidence:
    document: Document
    excerpt: str


@dataclass
class Candidate:
    """A retrieved product plus the backend's own verdict on each requirement (never model-supplied)."""

    product: Product
    match_type: str = "insufficient_data"
    satisfied: list[str] = field(default_factory=list)
    unmet: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    sort_key: tuple = ()
    documents: list[DocumentEvidence] = field(default_factory=list)


@dataclass
class Retrieval:
    candidates: list[Candidate]
    notes: list[str]


def _terms(criteria: SearchCriteria) -> list[str]:
    parsed = criteria.parsed
    values = [*parsed.required_features, *criteria.keywords]
    if parsed.intended_use:
        values.append(parsed.intended_use)
    return [value for value in values if value.strip()]


def _haystack(product: Product) -> str:
    parts = [
        product.name, product.brand, product.model, product.description, product.category,
        product.type, product.sub_type, product.key_benefits,
        *product.tags, *product.features, *product.use_cases,
        *(f"{s['key']} {s['value']}" for s in product.specifications),
    ]
    return " ".join(p for p in parts if p).lower()


def _score(text: str, terms: list[str]) -> float:
    score = 0.0
    for term in terms:
        tokens = [t for t in _WORD.findall(term.lower()) if len(t) >= 2]
        if tokens:
            score += sum(1 for t in tokens if t in text) / len(tokens)
    return score


def _excerpt(text: str, terms: list[str]) -> str:
    lines = text.splitlines()
    # Skip the title, mock-data notice and identification table; the body starts at the first section.
    start = next((i for i, line in enumerate(lines) if line.startswith("## ")), 0)
    body = [l.strip() for l in lines[start:] if l.strip() and not l.startswith("|---")]
    hits = [l for l in body if _score(l.lower(), terms) > 0]
    chosen = hits or body
    return " ".join(chosen)[:EXCERPT_CHARS]


def _load_documents(db: Session, product: Product, terms: list[str], notes: list[str]) -> list[DocumentEvidence]:
    evidence: list[tuple[float, int, DocumentEvidence]] = []
    for document in document_service.list_product_documents(db, product.id, limit=20, offset=0):
        try:
            text = document_service.read_document_text(document)
        except document_service.DocumentFileError:
            note = "Some supporting document files could not be read and were not used."
            if note not in notes:
                notes.append(note)
            continue
        order = DOCUMENT_PREFERENCE.index(document.document_type) if document.document_type in DOCUMENT_PREFERENCE else 99
        evidence.append((_score(text.lower(), terms), order, DocumentEvidence(document, _excerpt(text, terms))))
    evidence.sort(key=lambda item: (-item[0], item[1], item[2].document.id))
    return [item[2] for item in evidence[:DOCUMENTS_PER_PRODUCT]]


def _money(value: Decimal, currency: str) -> str:
    return f"{value:,.0f} {currency}"


def evaluate_product(
    product: Product, criteria: SearchCriteria, terms: list[str], *, category_matched: bool
) -> Candidate:
    """Check the hard constraints (category, brand, stock, budget, minimum RAM) against catalog data only."""
    parsed = criteria.parsed
    satisfied: list[str] = []
    unmet: list[str] = []
    unverified: list[str] = []
    over_ratio = 0.0
    ram_gap = 0.0

    if criteria.category_terms:
        label = " / ".join(criteria.category_terms)
        (satisfied if category_matched else unmet).append(
            f"Category: {label}" if category_matched else f"Not in the requested category ({label})"
        )
    if criteria.brand_filter:
        matches = (product.brand or "").lower() == criteria.brand_filter.lower()
        (satisfied if matches else unmet).append(
            f"Preferred brand: {criteria.brand_filter}"
            if matches else f"Different brand from the preferred {criteria.brand_filter}"
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
            satisfied.append(f"Within budget ({_money(price, product.currency)} of {_money(budget, product.currency)})")
        else:
            over_ratio = float((price - budget) / budget)
            unmet.append(f"Over budget by {_money(price - budget, product.currency)}")

    if criteria.min_ram_gb:
        ram = product_ram_gb(product)
        if ram is None:
            unverified.append(f"RAM of at least {criteria.min_ram_gb:g}GB could not be confirmed from the catalog")
        elif ram >= criteria.min_ram_gb:
            satisfied.append(f"RAM {ram:g}GB meets the requested {criteria.min_ram_gb:g}GB minimum")
        else:
            ram_gap = criteria.min_ram_gb - ram
            unmet.append(f"RAM {ram:g}GB is below the requested {criteria.min_ram_gb:g}GB")

    match_type = "alternative" if unmet else "insufficient_data" if unverified else "exact_match"
    tier = {"exact_match": 0, "insufficient_data": 1, "alternative": 2}[match_type]
    score = _score(_haystack(product), terms)
    return Candidate(
        product,
        match_type,
        satisfied,
        unmet,
        unverified,
        # Deterministic order: tier, fewest unmet requirements, closest to budget, closest RAM, relevance, id.
        (tier, len(unmet), over_ratio, ram_gap, -score, product.id),
    )


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
        return Retrieval([], ["The request is too general to search the catalog; more detail is needed."])

    def query(categories: list[str]) -> list[Product]:
        return product_service.search_products(
            db, category_terms=categories, in_stock_only=not include_out_of_stock, limit=POOL_SIZE
        )

    pool: list[Product] = []
    category_matched = bool(criteria.category_terms)
    if criteria.category_terms:
        pool = query(criteria.category_terms)
    if not pool and terms:
        # Uncertain category matching: fall back to keyword matching over the whole catalog.
        pool = [p for p in query([]) if _score(_haystack(p), terms) > 0]
        category_matched = not criteria.category_terms
        if pool and criteria.category_terms:
            notes.append("No products matched the category filter, so keyword matching was used instead.")

    evaluated = [evaluate_product(p, criteria, terms, category_matched=category_matched) for p in pool]
    if parsed.budget_max is not None:
        # Alternatives may exceed the budget, but not by an unreasonable margin.
        ceiling = parsed.budget_max * ALTERNATIVE_BUDGET_CEILING
        kept = [c for c in evaluated if c.product.currency.upper() != (parsed.currency or c.product.currency).upper()
                or c.product.selling_price <= ceiling]
        if len(kept) < len(evaluated):
            notes.append(
                f"{len(evaluated) - len(kept)} relevant product(s) were left out because they are far above the stated budget."
            )
        evaluated = kept

    if not evaluated:
        if not include_out_of_stock:
            notes.append("Out-of-stock and discontinued products were excluded from the search.")
        notes.append("No suitable product or meaningful alternative was found in the available catalog.")
        return Retrieval([], notes)

    evaluated.sort(key=lambda c: c.sort_key)
    candidates = evaluated[: min(max_products + 2, MAX_CANDIDATES)]
    for candidate in candidates:
        candidate.documents = _load_documents(db, candidate.product, terms, notes)
    return Retrieval(candidates, notes)
