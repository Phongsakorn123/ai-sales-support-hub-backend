from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "AI Sales Support Hub API"
    app_version: str = "1.0.0"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    database_path: Path = PROJECT_ROOT / "data" / "app.db"
    document_storage_dir: Path = PROJECT_ROOT / "storage" / "documents"
    jwt_secret_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    openrouter_model: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    semantic_embedding_model: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    semantic_embedding_cache_dir: Path = PROJECT_ROOT / "data" / "fastembed"
    retrieval_rrf_k: int = Field(default=60, ge=1, le=1000)
    retrieval_keyword_weight: float = Field(default=1.0, gt=0, le=10)
    retrieval_semantic_weight: float = Field(default=1.0, gt=0, le=10)

    @field_validator(
        "database_path", "document_storage_dir", "semantic_embedding_cache_dir"
    )
    @classmethod
    def resolve_database_path(cls, value: Path) -> Path:
        if value.is_absolute():
            return value
        return PROJECT_ROOT / value

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
