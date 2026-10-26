"""Structured outputs requested from the LLM. These are untrusted until validated by the services."""

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field


def _clean_text(max_length: int):
    def clean(value: object) -> object:
        return value.strip()[:max_length] if isinstance(value, str) else value

    return clean


def _clean_list(max_items: int, max_length: int):
    def clean(value: object) -> object:

        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return value
        items = [
            item.strip()[:max_length]
            for item in value
            if isinstance(item, str) and item.strip()
        ]
        return items[:max_items]

    return clean


ShortList = Annotated[list[str], BeforeValidator(_clean_list(10, 200))]


class ExtractedRequirements(BaseModel):
    """Stage A output: constraints extracted from the customer's natural-language request."""

    category: Annotated[str | None, BeforeValidator(_clean_text(100))] = Field(
        default=None,
        description="One product category or type taken from the provided catalog values, or null.",
    )
    max_budget: float | None = Field(
        default=None, description="Maximum budget as a number, or null if not stated."
    )
    currency: Annotated[str | None, BeforeValidator(_clean_text(3))] = Field(
        default=None,
        description="ISO 4217 currency code such as THB, or null if not stated.",
    )
    intended_use: Annotated[str | None, BeforeValidator(_clean_text(300))] = Field(
        default=None, description="What the customer will use the product for, or null."
    )
    required_features: ShortList = Field(
        default_factory=list,
        description="Features or specifications the customer requires.",
    )
    keywords: Annotated[list[str], BeforeValidator(_clean_list(8, 50))] = Field(
        default_factory=list, description="Up to 8 short search keywords."
    )
    preferred_brand: Annotated[str | None, BeforeValidator(_clean_text(100))] = Field(
        default=None,
        description="A brand taken from the provided catalog brands, or null.",
    )
    missing_information: ShortList = Field(
        default_factory=list,
        description="Important details the customer has not provided.",
    )
    clarification_questions: ShortList = Field(
        default_factory=list,
        description="Questions a salesperson could ask the customer.",
    )


class LLMRecommendation(BaseModel):
    product_id: int = Field(
        description="ID of a candidate product from the provided list."
    )
    reasons: Annotated[list[str], BeforeValidator(_clean_list(3, 300))] = Field(
        default_factory=list,
        description="Reasons grounded only in the provided product data or document excerpts.",
    )
    unmet_requirements: ShortList = Field(
        default_factory=list,
        description="Customer requirements this product does not demonstrably meet.",
    )
    follow_up_question: Annotated[str | None, BeforeValidator(_clean_text(200))] = (
        Field(
            default=None, description="One short question to ask the customer, or null."
        )
    )
    supporting_document_ids: Annotated[
        list[str], BeforeValidator(_clean_list(5, 20))
    ] = Field(
        default_factory=list,
        description="IDs of provided documents that support the reasons.",
    )


class RecommendationOutput(BaseModel):
    """Stage B output."""

    recommendations: list[LLMRecommendation] = Field(default_factory=list)
    limitations: ShortList = Field(
        default_factory=list,
        description="Gaps or uncertainties in the available evidence.",
    )
    summary: Annotated[str, BeforeValidator(_clean_text(600))] = Field(
        default="", description="One or two sentences summarizing the outcome."
    )
    intended_use: Annotated[str | None, BeforeValidator(_clean_text(300))] = Field(
        default=None,
        description="What the customer will use the product for, only if stated.",
    )
    required_features: ShortList = Field(
        default_factory=list, description="Other requirements the customer stated."
    )
    missing_information: ShortList = Field(
        default_factory=list,
        description="Important details the customer has not provided.",
    )
    clarification_questions: ShortList = Field(
        default_factory=list,
        description="Questions a salesperson could ask the customer.",
    )
