import asyncio
from functools import lru_cache
from typing import Protocol

from app.core.config import settings

# bge v1.5 retrieves better when short queries carry this instruction (documents don't)
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder(Protocol):
    dim: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class HFEmbedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # heavy import, deferred

        self.model = SentenceTransformer(model_name, device="cpu")
        self.query_prefix = BGE_QUERY_PREFIX if "bge" in model_name.lower() else ""
        self.dim = len(self._encode(["dim probe"])[0])

    def _encode(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(
            texts, normalize_embeddings=True, batch_size=32, convert_to_numpy=True
        ).tolist()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._encode, texts)

    async def embed_query(self, text: str) -> list[float]:
        [vector] = await asyncio.to_thread(self._encode, [self.query_prefix + text])
        return vector


@lru_cache
def get_embedder() -> Embedder:
    return HFEmbedder(settings.embedding_model)
