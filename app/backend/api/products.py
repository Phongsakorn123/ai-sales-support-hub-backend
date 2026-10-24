import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.backend.models.product import Product
from app.backend.dependencies.auth import get_current_user
from app.backend.schemas.product import ProductCreate, ProductRead, ProductUpdate
from app.backend.services import products as product_service
from app.backend.services.products import ProductPersistenceError
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/products",
    tags=["Products"],
    dependencies=[Depends(get_current_user)],
)


def _database_error(operation: str, error: ProductPersistenceError) -> HTTPException:
    logger.error(
        "Product %s failed",
        operation,
        exc_info=(type(error), error, error.__traceback__),
    )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="A database error occurred while processing the product request.",
    )


@router.post(
    "",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a product",
    responses={500: {"description": "Database operation failed"}},
)
def create_product(
    product_data: ProductCreate,
    db: Session = Depends(get_db),
) -> Product:
    try:
        return product_service.create_product(db, product_data)
    except ProductPersistenceError as error:
        raise _database_error("creation", error) from error


@router.get(
    "",
    response_model=list[ProductRead],
    summary="List active products",
    description="Returns active products only, ordered by ID. Inactive products remain retrievable by ID.",
    responses={500: {"description": "Database operation failed"}},
)
def list_products(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[Product]:
    try:
        return product_service.list_products(db, limit=limit, offset=offset)
    except ProductPersistenceError as error:
        raise _database_error("listing", error) from error


@router.get(
    "/{product_id}",
    response_model=ProductRead,
    summary="Get a product",
    responses={
        404: {"description": "Product not found"},
        500: {"description": "Database operation failed"},
    },
)
def get_product(product_id: int, db: Session = Depends(get_db)) -> Product:
    try:
        product = product_service.get_product(db, product_id)
    except ProductPersistenceError as error:
        raise _database_error("retrieval", error) from error
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


@router.patch(
    "/{product_id}",
    response_model=ProductRead,
    summary="Update a product",
    responses={
        404: {"description": "Product not found"},
        500: {"description": "Database operation failed"},
    },
)
def update_product(
    product_id: int,
    product_data: ProductUpdate,
    db: Session = Depends(get_db),
) -> Product:
    try:
        product = product_service.get_product(db, product_id)
        if product is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
        return product_service.update_product(db, product, product_data)
    except ProductPersistenceError as error:
        raise _database_error("update", error) from error


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a product",
    responses={
        404: {"description": "Product not found"},
        500: {"description": "Database operation failed"},
    },
)
def delete_product(product_id: int, db: Session = Depends(get_db)) -> Response:
    try:
        product = product_service.get_product(db, product_id)
        if product is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
        product_service.delete_product(db, product)
    except ProductPersistenceError as error:
        raise _database_error("deletion", error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
