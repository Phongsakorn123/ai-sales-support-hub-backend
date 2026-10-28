"""Extract customer requirements deterministically and validate them for retrieval."""

import math
from dataclasses import dataclass, field
from decimal import Decimal

from app.ai_agent.schemas.llm_outputs import ExtractedRequirements
from app.ai_agent.schemas.product_suggestion import ParsedRequirements
from app.ai_agent.services.rule_extraction import extract_by_rules, to_extracted

MAX_BUDGET = Decimal("1000000000")


@dataclass
class SearchCriteria:
    """Validated constraints. Only these values are ever used to filter the database."""

    parsed: ParsedRequirements
    category_terms: list[str] = field(default_factory=list)
    brand_filter: str | None = None
    keywords: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    min_ram_gb: float | None = None
    query_text: str = ""


def _canonical(value: str | None, allowed: list[str]) -> str | None:
    if not value:
        return None
    lookup = {item.lower(): item for item in allowed}
    return lookup.get(value.strip().lower())


def validate_requirements(
    extracted: ExtractedRequirements, taxonomy: dict[str, list[str]]
) -> SearchCriteria:
    notes: list[str] = []

    category_terms: list[str] = []
    category = _canonical(
        extracted.category, taxonomy["categories"] + taxonomy["types"]
    )
    if category:
        category_terms = [category]
    elif extracted.category:
        notes.append(
            f"The requested category '{extracted.category}' does not match a catalog category; "
            "results were found by keyword instead."
        )

    budget: Decimal | None = None
    if extracted.max_budget is not None:
        if (
            math.isfinite(extracted.max_budget)
            and 0 < extracted.max_budget <= MAX_BUDGET
        ):
            budget = Decimal(str(round(extracted.max_budget, 2)))
        else:
            notes.append(
                "The budget in the request could not be interpreted and was ignored."
            )

    currency = (
        extracted.currency.upper()
        if extracted.currency
        and extracted.currency.isalpha()
        and len(extracted.currency) == 3
        else None
    )
    if budget is not None and currency is None:
        notes.append(
            "The budget currency was not stated, so the budget was compared with catalog prices as listed."
        )

    brand_filter = _canonical(extracted.preferred_brand, taxonomy["brands"])
    if extracted.preferred_brand and brand_filter is None:
        notes.append(
            f"The preferred brand '{extracted.preferred_brand}' is not in the catalog."
        )

    parsed = ParsedRequirements(
        category=category or extracted.category,
        budget_max=budget,
        currency=currency,
        intended_use=extracted.intended_use,
        required_features=extracted.required_features,
        preferred_brand=extracted.preferred_brand,
        missing_information=extracted.missing_information,
        clarification_questions=extracted.clarification_questions,
    )
    return SearchCriteria(
        parsed=parsed,
        category_terms=category_terms,
        brand_filter=brand_filter,
        keywords=extracted.keywords,
        notes=notes,
    )


def build_criteria(requirements: str, taxonomy: dict[str, list[str]]) -> SearchCriteria:
    """Derive validated search criteria without a model call."""
    rules = extract_by_rules(requirements, taxonomy)
    criteria = validate_requirements(to_extracted(rules), taxonomy)
    criteria.query_text = requirements
    if rules.categories:
        criteria.category_terms = list(rules.categories)
        criteria.parsed.category = " / ".join(rules.categories)
    criteria.min_ram_gb = rules.min_ram_gb
    return criteria
