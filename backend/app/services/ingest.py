import asyncio
import io
import logging
import uuid

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.models import Document
from app.services.embeddings import Embedder
from app.services.vectorstore import Chunk, delete_document_chunks, upsert_chunks

logger = logging.getLogger(__name__)

ALLOWED_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


class IngestError(Exception):
    """Expected failure with a message safe to show the user."""


def extract_pages(data: bytes, ext: str) -> list[tuple[int | None, str]]:
    """Returns [(page_number, text)]. Page is None for plain-text files."""
    if ext == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and reader.decrypt("") == 0:
                raise IngestError("Password-protected PDFs are not supported")
            return [
                (i + 1, (page.extract_text() or "").replace("\x00", ""))
                for i, page in enumerate(reader.pages)
            ]
        except IngestError:
            raise
        except Exception as exc:
            raise IngestError("Could not read the PDF file") from exc

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return [(None, text)]


def chunk_pages(pages: list[tuple[int | None, str]]) -> list[Chunk]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap
    )
    chunks: list[Chunk] = []
    for page, text in pages:
        for piece in splitter.split_text(text):
            piece = piece.strip()
            if piece:
                chunks.append(Chunk(text=piece, chunk_index=len(chunks), page=page))
    return chunks


async def ingest_document(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    embedder: Embedder,
    qdrant: AsyncQdrantClient,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str,
    ext: str,
    data: bytes,
) -> None:
    """Background task: parse -> chunk -> embed -> upsert -> mark ready/failed."""
    status, chunk_count, error = "ready", 0, None
    try:
        pages = await asyncio.to_thread(extract_pages, data, ext)
        chunks = await asyncio.to_thread(chunk_pages, pages)
        if not chunks:
            raise IngestError("No extractable text found (scanned or image-only PDF?)")
        vectors = await embedder.embed_documents([c.text for c in chunks])
        await upsert_chunks(
            qdrant,
            user_id=user_id,
            document_id=document_id,
            filename=filename,
            chunks=chunks,
            vectors=vectors,
        )
        chunk_count = len(chunks)
    except IngestError as exc:
        status, error = "failed", str(exc)
    except Exception:
        logger.exception("Ingestion failed for document %s", document_id)
        status, error = "failed", "Processing failed"

    if status == "failed":
        await _cleanup_chunks(qdrant, document_id)

    async with sessionmaker() as s:
        doc = await s.get(Document, document_id)
        if doc is None:  # deleted while processing: drop what we wrote
            await _cleanup_chunks(qdrant, document_id)
            return
        doc.status, doc.chunk_count, doc.error = status, chunk_count, error
        await s.commit()


async def _cleanup_chunks(qdrant: AsyncQdrantClient, document_id: uuid.UUID) -> None:
    try:
        await delete_document_chunks(qdrant, document_id)
    except Exception:
        logger.exception("Failed to clean up chunks for document %s", document_id)