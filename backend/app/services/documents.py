import uuid
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from app.core.config import settings

VECTOR_SIZE = 384
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
SEARCH_LIMIT = 5


class InvalidDocumentError(ValueError):
    pass


def extract_pages(filename: str, content: bytes) -> list[tuple[int, str]]:
    extension = Path(filename).suffix.lower()
    if extension == ".txt":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InvalidDocumentError("Text files must use UTF-8 encoding") from exc
        if "\x00" in text:
            raise InvalidDocumentError("Text file contains binary data")
        pages = [(1, text)]
    elif extension == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted:
                raise InvalidDocumentError("Encrypted PDFs are not supported")
            pages = [
                (number, page.extract_text() or "")
                for number, page in enumerate(reader.pages, 1)
            ]
        except InvalidDocumentError:
            raise
        except Exception as exc:
            raise InvalidDocumentError("Could not read the PDF") from exc
    else:
        raise InvalidDocumentError("Only PDF and TXT files are supported")

    if not any(text.strip() for _, text in pages):
        raise InvalidDocumentError("Document contains no extractable text")
    return pages


def document_path(document_id: uuid.UUID, filename: str) -> Path:
    storage_path = settings.document_storage_path
    if not storage_path.is_absolute():
        storage_path = Path(__file__).resolve().parents[2] / storage_path
    return storage_path / f"{document_id}{Path(filename).suffix.lower()}"


def split_pages(pages: list[tuple[int, str]]) -> list[dict]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    chunks = []
    for page_number, text in pages:
        for chunk_index, chunk in enumerate(splitter.split_text(text)):
            if chunk.strip():
                chunks.append({"page": page_number, "chunk_index": chunk_index, "text": chunk})
    return chunks


@lru_cache(maxsize=1)
def get_embedding_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDING_MODEL, device="cpu")


def _ensure_collection(client, models) -> None:
    collection_name = settings.qdrant_collection
    if client.collection_exists(collection_name):
        return

    client.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(size=VECTOR_SIZE, distance=models.Distance.COSINE),
    )
    for field in ("user_id", "document_id"):
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )


def index_document(
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str,
    pages: list[tuple[int, str]],
) -> int:
    from qdrant_client import QdrantClient, models

    chunks = split_pages(pages)
    if not chunks:
        raise InvalidDocumentError("Document contains no extractable text")

    model = get_embedding_model()
    vectors = model.encode(
        [chunk["text"] for chunk in chunks],
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    points = [
        models.PointStruct(
            id=str(uuid.uuid5(document_id, str(index))),
            vector=vector.tolist(),
            payload={
                **chunk,
                "user_id": str(user_id),
                "document_id": str(document_id),
                "filename": filename,
            },
        )
        for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
    ]

    with QdrantClient(url=settings.qdrant_url) as client:
        _ensure_collection(client, models)
        client.upsert(collection_name=settings.qdrant_collection, points=points, wait=True)
    return len(chunks)


def search_documents(query: str, user_id: uuid.UUID) -> list[dict]:
    from qdrant_client import QdrantClient, models

    model = get_embedding_model()
    vector = model.encode(
        query,
        normalize_embeddings=True,
        show_progress_bar=False,
    ).tolist()

    with QdrantClient(url=settings.qdrant_url) as client:
        collection_name = settings.qdrant_collection
        if not client.collection_exists(collection_name):
            return []
        result = client.query_points(
            collection_name=collection_name,
            query=vector,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="user_id",
                        match=models.MatchValue(value=str(user_id)),
                    )
                ]
            ),
            limit=SEARCH_LIMIT,
            with_payload=True,
        )

    return [
        {**(point.payload or {}), "score": point.score}
        for point in result.points
        if point.payload and point.payload.get("text")
    ]


def delete_document_vectors(document_id: uuid.UUID) -> None:
    from qdrant_client import QdrantClient, models

    with QdrantClient(url=settings.qdrant_url) as client:
        collection_name = settings.qdrant_collection
        if not client.collection_exists(collection_name):
            return
        client.delete(
            collection_name=collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(value=str(document_id)),
                        )
                    ]
                )
            ),
            wait=True,
        )
