from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)


Price = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
StockQuantity = Annotated[int, Field(ge=0, strict=True)]
WarrantyMonths = Annotated[int, Field(ge=0, strict=True)]


# Collection items are trimmed and bounded to match the child-table columns,
# because SQLite does not enforce VARCHAR lengths.
SpecificationKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
SpecificationValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
FeatureItem = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
TagItem = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
LabelItem = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Sku = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class ProductSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: SpecificationKey
    value: SpecificationValue


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    selling_price: Price
    description: str | None = None
    category: str | None = Field(default=None, max_length=100)
    brand: str | None = Field(default=None, max_length=255)
    model: str | None = Field(default=None, max_length=255)
    sku: Sku | None = None
    image_url: str | None = None
    type: str | None = Field(default=None, max_length=100)
    sub_type: str | None = Field(default=None, max_length=100)
    cost_price: Price | None = None
    discontinued: bool = False
    warranty_months: WarrantyMonths | None = None
    specifications: list[ProductSpecification] = Field(default_factory=list)
    features: list[FeatureItem] = Field(default_factory=list)
    tags: list[TagItem] = Field(default_factory=list)
    target_customers: list[LabelItem] = Field(default_factory=list)
    use_cases: list[LabelItem] = Field(default_factory=list)
    key_benefits: str | None = None
    limitations: str | None = None
    currency: str = Field(default="THB", min_length=3, max_length=3)
    stock_quantity: StockQuantity = 0

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    selling_price: Price | None = None
    description: str | None = None
    category: str | None = Field(default=None, max_length=100)
    brand: str | None = Field(default=None, max_length=255)
    model: str | None = Field(default=None, max_length=255)
    sku: Sku | None = None
    image_url: str | None = None
    type: str | None = Field(default=None, max_length=100)
    sub_type: str | None = Field(default=None, max_length=100)
    cost_price: Price | None = None
    discontinued: bool | None = None
    warranty_months: WarrantyMonths | None = None
    specifications: list[ProductSpecification] | None = None
    features: list[FeatureItem] | None = None
    tags: list[TagItem] | None = None
    target_customers: list[LabelItem] | None = None
    use_cases: list[LabelItem] | None = None
    key_benefits: str | None = None
    limitations: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    stock_quantity: StockQuantity | None = None

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("name cannot be null")
        return value.strip() if isinstance(value, str) else value

    @field_validator(
        "selling_price",
        "currency",
        "stock_quantity",
        "discontinued",
        "specifications",
        "features",
        "tags",
        "target_customers",
        "use_cases",
        mode="before",
    )
    @classmethod
    def reject_null_for_required_fields(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("this field cannot be null")
        return value

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> "ProductUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        return self


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    category: str | None
    brand: str | None
    model: str | None
    sku: str | None
    image_url: str | None
    type: str | None
    sub_type: str | None
    selling_price: Decimal
    cost_price: Decimal | None
    currency: str
    stock_quantity: int
    discontinued: bool
    warranty_months: int | None
    specifications: list[ProductSpecification]
    features: list[str]
    tags: list[str]
    target_customers: list[str]
    use_cases: list[str]
    key_benefits: str | None
    limitations: str | None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def availability(self) -> Literal["discontinued", "out_of_stock", "in_stock"]:
        if self.discontinued:
            return "discontinued"
        return "in_stock" if self.stock_quantity > 0 else "out_of_stock"
