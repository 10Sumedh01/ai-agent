from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: str
    groq_model: str = "openai/gpt-oss-120b"
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    # qdrant_collection: str = "user_documents"
    document_storage_path: Path = Path("data/documents")
    document_max_size_mb: int = Field(default=10, ge=1)
    jwt_secret: str
    chat_rate_limit_per_minute: int = 10
    auth_login_rate_limit_per_minute: int = Field(default=10, ge=1)
    auth_register_rate_limit_per_minute: int = Field(default=5, ge=1)
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    qdrant_collection: str = "documents"
    chunk_size: int = 1000
    chunk_overlap: int = 150
    rag_top_k: int = 5


settings = Settings()