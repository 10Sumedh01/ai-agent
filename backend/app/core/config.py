from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    groq_api_key: str = ""
    groq_model:str="qwen/qwen3.8-27b"
    database_url: str
    redis_url: str
    qdrant_url: str
    jwt_secret: str = ""

    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()
