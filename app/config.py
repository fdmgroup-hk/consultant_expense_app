"""Application configuration, loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM provider ---
    llm_provider: str = "gemini"  # gemini | anthropic

    # --- Google Gemini (default; free tier) ---
    google_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    gemini_show_thinking: bool = True

    # --- Claude (optional alternative) ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    anthropic_effort: str = "high"
    anthropic_thinking_display: str = "summarized"
    anthropic_server_fallbacks: bool = True

    # --- embeddings ---
    embedding_provider: str = "auto"
    voyage_api_key: str = ""
    voyage_model: str = "voyage-3.5-lite"
    fastembed_model: str = "BAAI/bge-small-en-v1.5"

    # --- database ---
    # Empty -> SQLite on local disk. A postgresql:// URL -> Postgres (Supabase).
    database_url: str = ""
    db_pool_size: int = 5

    # --- storage ---
    data_dir: Path = PROJECT_ROOT / "data"
    storage_backend: str = "local"  # local | supabase | s3 | none

    # Supabase Storage (preferred when hosting on Supabase - no S3 keys needed)
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket: str = "consultant-originals"
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "ap-southeast-1"

    # --- access ---
    admin_token: str = "change-me-before-deploying"
    # Optional site-wide gate. Empty (the default) means anyone with the URL can
    # read and search; uploads always need the admin token regardless.
    app_password: str = ""
    # Comma-separated extra origins allowed to call the API cross-origin.
    allowed_origins: str = ""

    # --- retrieval ---
    retrieval_top_k: int = 8
    chunk_target_chars: int = 1100
    chunk_overlap_chars: int = 180

    @property
    def db_path(self) -> Path:
        return self.data_dir / "consultant_experience.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
