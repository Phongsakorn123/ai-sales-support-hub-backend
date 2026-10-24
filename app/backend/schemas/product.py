from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Price = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
StockQuantity = Annotated[int, Field(ge=0, strict=True)]


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    price: Price
    description: str | None = None
    category: str | None = Field(default=None, max_length=100)
    currency: str = Field(default="THB", min_length=3, max_length=3)
    stock_quantity: StockQuantity = 0
    is_active: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    price: Price | None = None
    description: str | None = None
    category: str | None = Field(default=None, max_length=100)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    stock_quantity: StockQuantity | None = None
    is_active: bool | None = None

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("name cannot be null")
        return value.strip() if isinstance(value, str) else value

    @field_validator("price", "currency", "stock_quantity", "is_active", mode="before")
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
    price: Decimal
    currency: str
    stock_quantity: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
