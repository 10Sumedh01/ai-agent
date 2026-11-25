import uuid
from dataclasses import dataclass
from functools import lru_cache

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from app.core.config import settings

UPSERT_BATCH = 256


@dataclass
class Chunk:
    text: str
    chunk_index: int
    page: int | None = None


@dataclass
class Hit:
    document_id: str
    filename: str
    page: int | None
    chunk_index: int
    text: str
    score: float


@lru_cache
def get_qdrant() -> AsyncQdrantClient:
    return AsyncQdrantClient(url=settings.qdrant_url)


async def ensure_collection(client: AsyncQdrantClient, dim: int) -> None:
    name = settings.qdrant_collection
    if not await client.collection_exists(name):
        await client.create_collection(
            name, vectors_config=VectorParams(size=dim, distance=Distance.COSINE)
        )
        for field in ("user_id", "document_id"):
            await client.create_payload_index(
                name, field_name=field, field_schema=PayloadSchemaType.KEYWORD
            )
        return

    info = await client.get_collection(name)
    existing = info.config.params.vectors.size
    if existing != dim:
        raise RuntimeError(
            f"Collection '{name}' has dim {existing}, embedding model gives {dim}. "
            "Changed the model? Delete the collection and re-ingest."
        )


async def upsert_chunks(
    client: AsyncQdrantClient,
    *,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    filename: str,
    chunks: list[Chunk],
    vectors: list[list[float]],
) -> None:
    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=vec,
            payload={
                "user_id": str(user_id),
                "document_id": str(document_id),
                "filename": filename,
                "page": c.page,
                "chunk_index": c.chunk_index,
                "text": c.text,
            },
        )
        for c, vec in zip(chunks, vectors, strict=True)
    ]
    for i in range(0, len(points), UPSERT_BATCH):
        await client.upsert(
            settings.qdrant_collection, points=points[i : i + UPSERT_BATCH], wait=True
        )


async def search(
    client: AsyncQdrantClient,
    *,
    user_id: uuid.UUID,
    vector: list[float],
    top_k: int,
    document_ids: list[uuid.UUID] | None = None,
) -> list[Hit]:
    # user_id filter is mandatory: tenant isolation in a shared collection
    must = [FieldCondition(key="user_id", match=MatchValue(value=str(user_id)))]
    if document_ids:
        must.append(
            FieldCondition(key="document_id", match=MatchAny(any=[str(d) for d in document_ids]))
        )
    res = await client.query_points(
        settings.qdrant_collection,
        query=vector,
        query_filter=Filter(must=must),
        limit=top_k,
        with_payload=True,
    )
    return [
        Hit(
            document_id=p.payload["document_id"],
            filename=p.payload["filename"],
            page=p.payload.get("page"),
            chunk_index=p.payload["chunk_index"],
            text=p.payload["text"],
            score=p.score,
        )
        for p in res.points
    ]


async def delete_document_chunks(client: AsyncQdrantClient, document_id: uuid.UUID) -> None:
    await client.delete(
        settings.qdrant_collection,
        points_selector=FilterSelector(
            filter=Filter(
                must=[FieldCondition(key="document_id", match=MatchValue(value=str(document_id)))]
            )
        ),
        wait=True,
    )