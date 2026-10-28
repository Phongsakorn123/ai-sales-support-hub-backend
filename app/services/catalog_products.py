"""Application use cases that coordinate catalog writes with derived AI data."""

import logging

from sqlalchemy.orm import Session

from app.ai_agent.services import embeddings
from app.backend.models.product import Product
from app.backend.schemas.product import ProductCreate, ProductUpdate
from app.backend.services import products as product_service

logger = logging.getLogger(__name__)


def _sync_product_embedding(db: Session, product: Product) -> None:
    try:
        embeddings.try_sync_product_embedding(db, product)
    except Exception as error:
        logger.warning(
            "Product embedding sync unavailable (product_id=%s, error=%s)",
            product.id,
            type(error).__name__,
        )


def create_product(db: Session, product_data: ProductCreate) -> Product:
    product = product_service.create_product(db, product_data)
    _sync_product_embedding(db, product)
    return product


def update_product(
    db: Session, product: Product, product_data: ProductUpdate
) -> Product:
    product = product_service.update_product(db, product, product_data)
    _sync_product_embedding(db, product)
    return product
