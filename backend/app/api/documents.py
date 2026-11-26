import logging
import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.models import Document, User
from app.db.session import get_db
from app.schemas.documents import DocumentOut
from app.services.documents import (
    InvalidDocumentError,
    delete_document_vectors,
    document_path,
    extract_pages,
    index_document,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


async def _index_saved_document(
    document: Document,
    pages: list[tuple[int, str]],
    db: AsyncSession,
) -> Document:
    try:
        chunk_count = await run_in_threadpool(
            index_document, document.id, document.user_id, document.filename, pages
        )
    except Exception as exc:
        logger.exception("Document indexing failed for document %s", document.id)
        document.status = "failed"
        document.error = "Document indexing failed"
        await db.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Document was stored but could not be indexed; retry it later",
        ) from exc

    document.status = "ready"
    document.chunk_count = chunk_count
    document.error = None
    await db.commit()
    await db.refresh(document)
    return document


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    original_name = file.filename or ""
    filename = original_name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not filename or len(filename) > 255:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid filename")

    max_bytes = settings.document_max_size_mb * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Document is too large")

    try:
        pages = await run_in_threadpool(extract_pages, filename, content)
    except InvalidDocumentError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    document_id = uuid.uuid4()
    path = document_path(document_id, filename)
    try:
        await run_in_threadpool(path.parent.mkdir, parents=True, exist_ok=True)
        await run_in_threadpool(path.write_bytes, content)
    except OSError as exc:
        logger.exception("Could not store uploaded document")
        try:
            await run_in_threadpool(path.unlink, missing_ok=True)
        except OSError:
            logger.exception("Could not clean up partial document file %s", path)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Could not store document") from exc

    document = Document(
        id=document_id,
        user_id=user.id,
        filename=filename,
        content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
        size_bytes=len(content),
    )
    try:
        db.add(document)
        await db.commit()
    except Exception:
        await db.rollback()
        try:
            await run_in_threadpool(path.unlink, missing_ok=True)
        except OSError:
            logger.exception("Could not clean up document file %s after database failure", path)
        raise
    await db.refresh(document)

    return await _index_saved_document(document, pages, db)


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.scalars(
        select(Document)
        .where(Document.user_id == user.id)
        .order_by(Document.created_at.desc())
    )
    return result.all()


@router.post("/{document_id}/retry", response_model=DocumentOut)
async def retry_document_indexing(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    document = await db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == user.id,
        )
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if document.status != "failed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only failed documents can be retried",
        )

    path = document_path(document.id, document.filename)
    try:
        content = await run_in_threadpool(path.read_bytes)
    except OSError as exc:
        logger.exception("Could not read stored document %s for retry", document.id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Stored document is unavailable; indexing cannot be retried",
        ) from exc

    try:
        pages = await run_in_threadpool(extract_pages, document.filename, content)
    except InvalidDocumentError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    return await _index_saved_document(document, pages, db)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    document = await db.scalar(
        select(Document).where(Document.id == document_id, Document.user_id == user.id)
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    try:
        await run_in_threadpool(delete_document_vectors, document.id)
    except Exception as exc:
        logger.exception("Could not remove vectors for document %s", document.id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not remove document from search index",
        ) from exc

    path = document_path(document.id, document.filename)
    try:
        await run_in_threadpool(path.unlink, missing_ok=True)
    except OSError as exc:
        logger.exception("Could not remove stored document %s", document.id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Could not remove document file"
        ) from exc

    deleted_document_id = document.id
    await db.delete(document)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception("Could not remove document record %s", deleted_document_id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Document cleanup is incomplete; retry the deletion",
        ) from exc
