from datetime import datetime, timezone

from decimal import Decimal

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.backend.models.product import (
    Product,
    ProductFeature,
    ProductSpecification,
    ProductTag,
    ProductTargetCustomer,
    ProductUseCase,
)
from app.backend.repositories import products as product_repository
from app.backend.schemas.product import ProductCreate, ProductUpdate


class ProductPersistenceError(Exception):
    pass


class DuplicateSkuError(ProductPersistenceError):
    pass


def _raise_persistence_error(error: SQLAlchemyError, message: str) -> None:
    if isinstance(error, IntegrityError) and "products.sku" in str(error.orig):
        raise DuplicateSkuError("SKU already exists") from error
    raise ProductPersistenceError(message) from error


COLLECTION_FIELDS = (
    "specifications",
    "features",
    "tags",
    "target_customers",
    "use_cases",
)


def _set_collections(product: Product, collections: dict[str, list]) -> None:

    if "specifications" in collections:
        product.specification_rows = [
            ProductSpecification(key=item["key"], value=item["value"], position=index)
            for index, item in enumerate(collections["specifications"])
        ]
    simple = {
        "features": ("feature_rows", ProductFeature),
        "tags": ("tag_rows", ProductTag),
        "target_customers": ("target_customer_rows", ProductTargetCustomer),
        "use_cases": ("use_case_rows", ProductUseCase),
    }
    for field, (attribute, model) in simple.items():
        if field in collections:
            setattr(
                product,
                attribute,
                [
                    model(value=value, position=index)
                    for index, value in enumerate(collections[field])
                ],
            )


def create_product(db: Session, product_data: ProductCreate) -> Product:
    values = product_data.model_dump()
    collections = {field: values.pop(field) for field in COLLECTION_FIELDS}
    product = Product(**values)
    _set_collections(product, collections)
    try:
        product_repository.add_product(db, product)
    except SQLAlchemyError as error:
        db.rollback()
        _raise_persistence_error(error, "Could not create product")
    return product


def list_products(db: Session, *, limit: int, offset: int) -> list[Product]:
    try:
        return product_repository.list_products(db, limit=limit, offset=offset)
    except SQLAlchemyError as error:
        db.rollback()
        raise ProductPersistenceError("Could not list products") from error


def get_product(db: Session, product_id: int) -> Product | None:
    try:
        return product_repository.get_product(db, product_id)
    except SQLAlchemyError as error:
        db.rollback()
        raise ProductPersistenceError("Could not retrieve product") from error


def update_product(
    db: Session,
    product: Product,
    product_data: ProductUpdate,
) -> Product:
    values = product_data.model_dump(exclude_unset=True)
    collections = {
        field: values.pop(field) for field in COLLECTION_FIELDS if field in values
    }
    for field, value in values.items():
        setattr(product, field, value)
    _set_collections(product, collections)
    product.updated_at = datetime.now(timezone.utc)
    try:
        product_repository.save_product(db, product)
    except SQLAlchemyError as error:
        db.rollback()
        _raise_persistence_error(error, "Could not update product")
    return product


def delete_product(db: Session, product: Product) -> None:
    try:
        product_repository.delete_product(db, product)
    except SQLAlchemyError as error:
        db.rollback()
        raise ProductPersistenceError("Could not delete product") from error


def search_products(
    db: Session,
    *,
    category_terms: list[str] | None = None,
    brand: str | None = None,
    max_price: Decimal | None = None,
    currency: str | None = None,
    in_stock_only: bool = False,
    limit: int = 50,
) -> list[Product]:
    """Filter products with parameterized ORM clauses; every filter is optional and combined with AND."""
    try:
        return product_repository.search_products(
            db,
            category_terms=category_terms,
            brand=brand,
            max_price=max_price,
            currency=currency,
            in_stock_only=in_stock_only,
            limit=limit,
        )
    except SQLAlchemyError as error:
        db.rollback()
        raise ProductPersistenceError("Could not search products") from error


def list_catalog_taxonomy(db: Session) -> dict[str, list[str]]:
    """Distinct category, type/sub-type and brand values currently present in the catalog."""
    try:
        return product_repository.get_catalog_taxonomy(db)
    except SQLAlchemyError as error:
        db.rollback()
        raise ProductPersistenceError("Could not read catalog taxonomy") from error
