import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.backend.dependencies.auth import get_current_user
from app.backend.models.document import Document
from app.backend.models.product import Product
from app.backend.schemas.document import (
    DocumentRead,
    DocumentSearchResponse,
    DocumentSearchResult,
)
from app.backend.services import documents as document_service
from app.backend.services.documents import (
    DOCUMENT_ID_PATTERN,
    DocumentFileError,
    DocumentPersistenceError,
)
from app.core.config import settings
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/documents",
    tags=["Documents"],
    dependencies=[Depends(get_current_user)],
)
product_documents_router = APIRouter(
    prefix="/products",
    tags=["Documents"],
    dependencies=[Depends(get_current_user)],
)

DocumentIdPath = Path(
    pattern=DOCUMENT_ID_PATTERN,
    description="Document ID such as `DOC-0001`.",
    examples=["DOC-0001"],
)
DOCUMENT_ERRORS = {
    404: {"description": "Document or file not found"},
    500: {"description": "Database operation failed"},
}


def _database_error(operation: str, error: Exception) -> HTTPException:
    logger.error(
        "Document %s failed",
        operation,
        exc_info=(type(error), error, error.__traceback__),
    )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="A database error occurred while processing the document request.",
    )


def _urls(document: Document) -> dict[str, str]:
    base = f"{settings.api_prefix}/documents/{document.id}/file"
    return {"file_url": base, "download_url": f"{base}?download=true"}


def to_read(document: Document) -> DocumentRead:
    data = {
        name: getattr(document, name)
        for name in DocumentRead.model_fields
        if name not in ("file_url", "download_url")
    }
    return DocumentRead(**data, **_urls(document))


@router.get(
    "/search",
    response_model=DocumentSearchResponse,
    summary="Search supporting documents",
    description=(
        "Keyword search over document title, description and Markdown text (case-insensitive; every "
        "word in `q` must appear). Filters and pagination are optional. This is plain keyword "
        "matching, not semantic search."
    ),
    responses={500: DOCUMENT_ERRORS[500]},
)
def search_documents(
    q: str | None = Query(default=None, max_length=200, description="Keywords."),
    product_id: int | None = Query(default=None, ge=1),
    document_type: str | None = Query(
        default=None, pattern=r"^[a-z_]{1,50}$", examples=["datasheet"]
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> DocumentSearchResponse:
    try:
        hits, total = document_service.search_documents(
            db,
            q=q.strip() if q else None,
            product_id=product_id,
            document_type=document_type,
            limit=limit,
            offset=offset,
        )
    except DocumentPersistenceError as error:
        raise _database_error("search", error) from error
    items = [
        DocumentSearchResult(**to_read(hit.document).model_dump(), snippet=hit.snippet)
        for hit in hits
    ]
    return DocumentSearchResponse(items=items, total=total, limit=limit, offset=offset)


def _get_or_404(db: Session, document_id: str) -> Document:
    try:
        document = document_service.get_document(db, document_id)
    except DocumentPersistenceError as error:
        raise _database_error("lookup", error) from error
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return document


@router.get(
    "/{document_id}",
    response_model=DocumentRead,
    summary="Get document metadata",
    responses=DOCUMENT_ERRORS,
)
def get_document(
    document_id: str = DocumentIdPath,
    db: Session = Depends(get_db),
) -> DocumentRead:
    return to_read(_get_or_404(db, document_id))


@router.get(
    "/{document_id}/file",
    summary="Open or download the Markdown file",
    description="Returns the actual `.md` file. Use `download=true` for an attachment.",
    response_class=Response,
    responses={
        200: {"content": {"text/markdown": {}}, "description": "The Markdown document"},
        **DOCUMENT_ERRORS,
    },
)
def get_document_file(
    document_id: str = DocumentIdPath,
    download: bool = Query(default=False, description="Send as an attachment."),
    db: Session = Depends(get_db),
) -> Response:
    document = _get_or_404(db, document_id)
    try:
        content = document_service.read_document_bytes(document)
    except DocumentFileError as error:
        logger.warning("File for %s unavailable: %s", document.id, error)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document file not found."
        ) from None
    disposition = "attachment" if download else "inline"
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={
            # The filename comes from the validated document ID, never from the stored path.
            "Content-Disposition": f'{disposition}; filename="{document.id}.md"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@product_documents_router.get(
    "/{product_id}/documents",
    response_model=list[DocumentRead],
    summary="List a product's documents",
    responses={404: {"description": "Product not found"}, 500: DOCUMENT_ERRORS[500]},
)
def list_product_documents(
    product_id: int = Path(ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[DocumentRead]:
    try:
        if db.get(Product, product_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")
        documents = document_service.list_product_documents(
            db, product_id, limit=limit, offset=offset
        )
    except (DocumentPersistenceError, SQLAlchemyError) as error:
        db.rollback()
        raise _database_error("listing", error) from error
    return [to_read(document) for document in documents]
