import uuid

from qdrant_client import AsyncQdrantClient

from app.core.config import settings
from app.services.embeddings import Embedder
from app.services.vectorstore import Hit, search

SNIPPET_CHARS = 300
NO_CONTEXT = "(no relevant passages were found in the user's documents)"


async def retrieve(
    embedder: Embedder,
    qdrant: AsyncQdrantClient,
    *,
    user_id: uuid.UUID,
    query: str,
    document_ids: list[uuid.UUID] | None = None,
    top_k: int | None = None,
) -> list[Hit]:
    vector = await embedder.embed_query(query)
    return await search(
        qdrant,
        user_id=user_id,
        vector=vector,
        top_k=top_k or settings.rag_top_k,
        document_ids=document_ids,
    )


def build_context(hits: list[Hit]) -> str:
    if not hits:
        return NO_CONTEXT
    blocks = []
    for i, h in enumerate(hits, 1):
        where = f"{h.filename}, page {h.page}" if h.page else h.filename
        blocks.append(f"[{i}] ({where})\n{h.text}")
    return "\n\n".join(blocks)


def to_sources(hits: list[Hit]) -> list[dict]:
    """JSON-serialisable citations, stored in messages.sources and sent to the client."""
    return [
        {
            "index": i,
            "document_id": h.document_id,
            "filename": h.filename,
            "page": h.page,
            "chunk_index": h.chunk_index,
            "score": round(h.score, 4),
            "snippet": h.text[:SNIPPET_CHARS],
        }
        for i, h in enumerate(hits, 1)
    ]
