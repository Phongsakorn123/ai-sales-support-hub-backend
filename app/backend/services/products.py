from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.backend.models.product import Product
from app.backend.schemas.product import ProductCreate, ProductUpdate


class ProductPersistenceError(Exception):
    pass


def create_product(db: Session, product_data: ProductCreate) -> Product:
    product = Product(**product_data.model_dump())
    try:
        db.add(product)
        db.commit()
        db.refresh(product)
    except SQLAlchemyError as error:
        db.rollback()
        raise ProductPersistenceError("Could not create product") from error
    return product


def list_products(db: Session, *, limit: int, offset: int) -> list[Product]:
    statement = (
        select(Product)
        .where(Product.is_active.is_(True))
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
        return db.get(Product, product_id)
    except SQLAlchemyError as error:
        db.rollback()
        raise ProductPersistenceError("Could not retrieve product") from error


def update_product(
    db: Session,
    product: Product,
    product_data: ProductUpdate,
) -> Product:
    for field, value in product_data.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    product.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
        db.refresh(product)
    except SQLAlchemyError as error:
        db.rollback()
        raise ProductPersistenceError("Could not update product") from error
    return product


def delete_product(db: Session, product: Product) -> None:
    try:
        db.delete(product)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise ProductPersistenceError("Could not delete product") from error
