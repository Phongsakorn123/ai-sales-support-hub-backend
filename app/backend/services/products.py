from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.backend.models.product import (
    Product,
    ProductFeature,
    ProductSpecification,
    ProductTag,
    ProductTargetCustomer,
    ProductUseCase,
)
from app.backend.schemas.product import ProductCreate, ProductUpdate


class ProductPersistenceError(Exception):
    pass


class DuplicateSkuError(ProductPersistenceError):
    pass


def _raise_persistence_error(error: SQLAlchemyError, message: str) -> None:
    if isinstance(error, IntegrityError) and "products.sku" in str(error.orig):
        raise DuplicateSkuError("SKU already exists") from error
    raise ProductPersistenceError(message) from error


# Eager-load every child collection with one extra query each, avoiding N+1 lookups.
EAGER_COLLECTIONS = (
    selectinload(Product.specification_rows),
    selectinload(Product.feature_rows),
    selectinload(Product.tag_rows),
    selectinload(Product.target_customer_rows),
    selectinload(Product.use_case_rows),
)

COLLECTION_FIELDS = ("specifications", "features", "tags", "target_customers", "use_cases")


def _set_collections(product: Product, collections: dict[str, list]) -> None:
    # Reassigning the relationship lists replaces rows; delete-orphan removes the old ones.
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
                [model(value=value, position=index) for index, value in enumerate(collections[field])],
            )


def create_product(db: Session, product_data: ProductCreate) -> Product:
    values = product_data.model_dump()
    collections = {field: values.pop(field) for field in COLLECTION_FIELDS}
    product = Product(**values)
    _set_collections(product, collections)
    try:
        db.add(product)
        db.commit()
        db.refresh(product)
    except SQLAlchemyError as error:
        db.rollback()
        _raise_persistence_error(error, "Could not create product")
    return product


def list_products(db: Session, *, limit: int, offset: int) -> list[Product]:
    statement = (
        select(Product)
        .options(*EAGER_COLLECTIONS)
        .order_by(Product.id)
        .limit(limit)
        .offset(offset)
    )
    try:
        return list(db.scalars(statement).all())
    except SQLAlchemyError as error:
        db.rollback()
        raise ProductPersistenceError("Could not list products") from error


def get_product(db: Session, product_id: int) -> Product | None:
    try:
        return db.get(Product, product_id, options=EAGER_COLLECTIONS)
    except SQLAlchemyError as error:
        db.rollback()
        raise ProductPersistenceError("Could not retrieve product") from error


def update_product(
    db: Session,
    product: Product,
    product_data: ProductUpdate,
) -> Product:
    values = product_data.model_dump(exclude_unset=True)
    collections = {field: values.pop(field) for field in COLLECTION_FIELDS if field in values}
    for field, value in values.items():
        setattr(product, field, value)
    _set_collections(product, collections)
    product.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
        db.refresh(product)
    except SQLAlchemyError as error:
        db.rollback()
        _raise_persistence_error(error, "Could not update product")
    return product


def delete_product(db: Session, product: Product) -> None:
    try:
        db.delete(product)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise ProductPersistenceError("Could not delete product") from error
