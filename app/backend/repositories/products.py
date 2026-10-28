"""SQLAlchemy persistence operations for catalog products."""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.backend.models.product import Product

EAGER_COLLECTIONS = (
    selectinload(Product.specification_rows),
    selectinload(Product.feature_rows),
    selectinload(Product.tag_rows),
    selectinload(Product.target_customer_rows),
    selectinload(Product.use_case_rows),
)


def add_product(db: Session, product: Product) -> Product:
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def list_products(db: Session, *, limit: int, offset: int) -> list[Product]:
    statement = (
        select(Product)
        .options(*EAGER_COLLECTIONS)
        .order_by(Product.id)
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(statement).all())


def get_product(db: Session, product_id: int) -> Product | None:
    return db.get(Product, product_id, options=EAGER_COLLECTIONS)


def save_product(db: Session, product: Product) -> Product:
    db.commit()
    db.refresh(product)
    return product


def delete_product(db: Session, product: Product) -> None:
    db.delete(product)
    db.commit()


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
    statement = select(Product).options(*EAGER_COLLECTIONS)
    if category_terms:
        terms = [term.lower() for term in category_terms]
        statement = statement.where(
            Product.category.is_not(None) & func.lower(Product.category).in_(terms)
            | Product.type.is_not(None) & func.lower(Product.type).in_(terms)
            | Product.sub_type.is_not(None) & func.lower(Product.sub_type).in_(terms)
        )
    if brand:
        statement = statement.where(func.lower(Product.brand) == brand.lower())
    if max_price is not None:
        statement = statement.where(Product.selling_price <= max_price)
    if currency:
        statement = statement.where(func.upper(Product.currency) == currency.upper())
    if in_stock_only:
        statement = statement.where(
            Product.stock_quantity > 0, Product.discontinued.is_(False)
        )
    return list(db.scalars(statement.order_by(Product.id).limit(limit)).all())


def get_catalog_taxonomy(db: Session) -> dict[str, list[str]]:
    categories = {
        value for value in db.scalars(select(Product.category).distinct()) if value
    }
    types = {
        value
        for column in (Product.type, Product.sub_type)
        for value in db.scalars(select(column).distinct())
        if value
    }
    brands = {
        value for value in db.scalars(select(Product.brand).distinct()) if value
    }
    return {
        "categories": sorted(categories),
        "types": sorted(types),
        "brands": sorted(brands),
    }
