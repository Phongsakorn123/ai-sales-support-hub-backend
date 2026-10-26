from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, StringConstraints
from typing import Annotated

Requirements = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=2000)]


class ProductSuggestionRequest(BaseModel):
    requirements: Requirements = Field(
        description="The customer's needs in natural language.",
        examples=["A laptop for video editing under 50000 THB with at least 32GB RAM"],
    )
    max_products: int = Field(default=3, ge=1, le=5, description="Maximum number of recommendations.")
    include_out_of_stock: bool = Field(
        default=False, description="Also consider out-of-stock and discontinued products."
    )


class ParsedRequirements(BaseModel):
    category: str | None = None
    budget_max: Decimal | None = None
    currency: str | None = None
    intended_use: str | None = None
    required_features: list[str] = []
    preferred_brand: str | None = None
    missing_information: list[str] = []
    clarification_questions: list[str] = []


class SupportingDocument(BaseModel):
    document_id: str
    title: str
    document_type: str
    is_mock: bool = Field(description="True means demo data, not verified manufacturer documentation.")
    approval_status: str
    file_url: str


MatchType = Literal["exact_match", "alternative", "insufficient_data"]


class ProductRecommendation(BaseModel):
    product_id: int
    name: str
    brand: str | None
    sku: str | None
    category: str | None
    selling_price: Decimal
    currency: str
    in_stock: bool
    stock_quantity: int
    match_type: MatchType = Field(
        default="insufficient_data",
        description="`exact_match`: every checkable hard constraint (category, budget, stock, minimum RAM) "
        "is met. `alternative`: relevant, but a requirement is not met. `insufficient_data`: nothing "
        "failed, but a requirement could not be verified from catalog data. Decided by the backend.",
    )
    reasons: list[str]
    satisfied_requirements: list[str] = Field(default=[], description="Requirements verified from catalog data.")
    unmet_requirements: list[str] = Field(default=[], description="Requirements known not to be met.")
    unverified_requirements: list[str] = Field(
        default=[], description="Requirements that could not be verified because catalog data is missing."
    )
    follow_up_question: str | None = Field(
        default=None, description="A question the salesperson could ask the customer."
    )
    supporting_documents: list[SupportingDocument]


class ProductSuggestionResponse(BaseModel):
    parsed_requirements: ParsedRequirements
    recommendations: list[ProductRecommendation]
    summary: str
    limitations: list[str]
    data_sufficiency: Literal["sufficient", "partial", "insufficient"] = Field(
        description="`insufficient` when nothing could be recommended; `partial` when evidence is "
        "incomplete or demo-only; `sufficient` otherwise."
    )
    candidates_considered: int
    exact_match_count: int = Field(default=0, description="Recommendations that fully meet the hard constraints.")
    model: str
    ranking_source: Literal["ai", "catalog_fallback"] = Field(
        default="ai",
        description="`catalog_fallback` means the AI model was unavailable and the products are filter "
        "matches only, without AI ranking or reasoning.",
    )
    retrieval_mode: Literal["hybrid", "keyword_fallback"] = Field(
        default="keyword_fallback",
        description="`keyword_fallback` means semantic retrieval was unavailable or had no indexed products.",
    )
    disclaimer: str
