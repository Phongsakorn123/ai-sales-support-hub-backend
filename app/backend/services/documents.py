import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.backend.models.document import Document
from app.core.config import settings

logger = logging.getLogger(__name__)

DOCUMENT_ID_PATTERN = r"^DOC-\d{4,9}$"
SNIPPET_RADIUS = 80


class DocumentPersistenceError(Exception):
    pass


class DocumentFileError(Exception):
    """The file behind a document record is missing, unreadable or outside storage."""


def storage_root() -> Path:
    return settings.document_storage_dir.resolve()


def resolve_document_path(document: Document) -> Path:
    """Resolve the stored relative path and make sure it stays inside the storage directory."""
    root = storage_root()
    relative = Path(document.file_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise DocumentFileError("Stored path is not a safe relative path.")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise DocumentFileError("Stored path escapes the storage directory.")
    if not candidate.is_file():
        raise DocumentFileError("Document file does not exist.")
    return candidate


def read_document_text(document: Document) -> str:
    path = resolve_document_path(document)
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise DocumentFileError("Document file could not be read.") from error


def read_document_bytes(document: Document) -> bytes:
    path = resolve_document_path(document)
    try:
        return path.read_bytes()
    except OSError as error:
        raise DocumentFileError("Document file could not be read.") from error


_cache: dict[Path, tuple[int, int, str]] = {}
_cache_lock = threading.Lock()


def _cached_text(document: Document) -> str | None:
    try:
        path = resolve_document_path(document)
        stat = path.stat()
    except (DocumentFileError, OSError):
        return None
    signature = (stat.st_mtime_ns, stat.st_size)
    with _cache_lock:
        entry = _cache.get(path)
        if entry and entry[:2] == signature:
            return entry[2]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    with _cache_lock:
        _cache[path] = (*signature, text)
    return text


def clear_search_cache() -> None:
    with _cache_lock:
        _cache.clear()


@dataclass
class SearchHit:
    document: Document
    snippet: str | None


def _snippet(text: str, term: str) -> str | None:
    index = text.lower().find(term)
    if index < 0:
        return None
    start = max(0, index - SNIPPET_RADIUS)
    end = min(len(text), index + len(term) + SNIPPET_RADIUS)
    excerpt = re.sub(r"\s+", " ", text[start:end]).strip()
    return ("…" if start else "") + excerpt + ("…" if end < len(text) else "")


def search_documents(
    db: Session,
    *,
    q: str | None,
    product_id: int | None,
    document_type: str | None,
    limit: int,
    offset: int,
) -> tuple[list[SearchHit], int]:
    statement = select(Document).order_by(Document.id)
    if product_id is not None:
        statement = statement.where(Document.product_id == product_id)
    if document_type:
        statement = statement.where(Document.document_type == document_type)
    try:
        candidates = list(db.scalars(statement))
    except SQLAlchemyError as error:
        db.rollback()
        raise DocumentPersistenceError from error

    terms = (q or "").lower().split()
    if not terms:
        hits = [SearchHit(document, None) for document in candidates]
    else:
        hits = []
        for document in candidates:
            content = _cached_text(document) or ""
            haystack = (
                f"{document.title}\n{document.description or ''}\n{content}".lower()
            )
            if all(term in haystack for term in terms):
                snippet = _snippet(content, terms[0]) if content else None
                hits.append(SearchHit(document, snippet))
    return hits[offset : offset + limit], len(hits)


def get_document(db: Session, document_id: str) -> Document | None:
    try:
        return db.get(Document, document_id)
    except SQLAlchemyError as error:
        db.rollback()
        raise DocumentPersistenceError from error


def list_product_documents(
    db: Session, product_id: int, *, limit: int, offset: int
) -> list[Document]:
    try:
        return list(
            db.scalars(
                select(Document)
                .where(Document.product_id == product_id)
                .order_by(Document.id)
                .limit(limit)
                .offset(offset)
            )
        )
    except SQLAlchemyError as error:
        db.rollback()
        raise DocumentPersistenceError from error
