from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Numeric, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UTCDateTime


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sub_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    selling_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3),
        default="THB",
        server_default=text("'THB'"),
        nullable=False,
    )
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    stock_quantity: Mapped[int] = mapped_column(
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    discontinued: Mapped[bool] = mapped_column(
        default=False,
        server_default=text("0"),
        nullable=False,
    )
    warranty_months: Mapped[int | None] = mapped_column(nullable=True)
    key_benefits: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitations: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.current_timestamp(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.current_timestamp(),
        nullable=False,
    )

    specification_rows: Mapped[list["ProductSpecification"]] = relationship(
        order_by="ProductSpecification.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    feature_rows: Mapped[list["ProductFeature"]] = relationship(
        order_by="ProductFeature.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    tag_rows: Mapped[list["ProductTag"]] = relationship(
        order_by="ProductTag.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    target_customer_rows: Mapped[list["ProductTargetCustomer"]] = relationship(
        order_by="ProductTargetCustomer.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    use_case_rows: Mapped[list["ProductUseCase"]] = relationship(
        order_by="ProductUseCase.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def specifications(self) -> list[dict[str, str]]:
        return [{"key": row.key, "value": row.value} for row in self.specification_rows]

    @property
    def features(self) -> list[str]:
        return [row.value for row in self.feature_rows]

    @property
    def tags(self) -> list[str]:
        return [row.value for row in self.tag_rows]

    @property
    def target_customers(self) -> list[str]:
        return [row.value for row in self.target_customer_rows]

    @property
    def use_cases(self) -> list[str]:
        return [row.value for row in self.use_case_rows]


def _product_fk() -> Mapped[int]:
    return mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )


class ProductSpecification(Base):
    __tablename__ = "product_specifications"
    __table_args__ = (Index("ix_product_specifications_product_id", "product_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = _product_fk()
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(
        default=0, server_default=text("0"), nullable=False
    )


class ProductFeature(Base):
    __tablename__ = "product_features"
    __table_args__ = (Index("ix_product_features_product_id", "product_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = _product_fk()
    value: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(
        default=0, server_default=text("0"), nullable=False
    )


class ProductTag(Base):
    __tablename__ = "product_tags"
    __table_args__ = (
        Index("ix_product_tags_product_id", "product_id"),
        Index("ix_product_tags_value", "value"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = _product_fk()
    value: Mapped[str] = mapped_column(String(100), nullable=False)
    position: Mapped[int] = mapped_column(
        default=0, server_default=text("0"), nullable=False
    )


class ProductTargetCustomer(Base):
    __tablename__ = "product_target_customers"
    __table_args__ = (Index("ix_product_target_customers_product_id", "product_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = _product_fk()
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(
        default=0, server_default=text("0"), nullable=False
    )


class ProductUseCase(Base):
    __tablename__ = "product_use_cases"
    __table_args__ = (Index("ix_product_use_cases_product_id", "product_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = _product_fk()
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(
        default=0, server_default=text("0"), nullable=False
    )
