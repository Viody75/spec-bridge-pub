from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str | None = None
    specbridge_api_key: SecretStr | None = None
    disable_api_key_auth: bool = False
    model_name: str = "gemini-3.8-flash"
    text_embedding_model: str | None = None
    auto_create_schema: bool = False
    max_sources_per_run: int = Field(default=50, ge=1, le=500)
    max_normalized_characters: int = Field(default=100_000, ge=1_000)
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    # Cloud Run rejects HTTP/1 request bodies above 32 MiB, so a batch stays below it
    max_upload_files: int = Field(default=10, ge=1, le=50)
    max_upload_batch_bytes: int = Field(default=30 * 1024 * 1024, ge=1024)
    max_pdf_pages: int = Field(default=200, ge=1, le=2_000)
    max_docx_uncompressed_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    # OCR for scanned PDF pages and images (whiteboards, screenshots) uses Gemini
    ocr_enabled: bool = True
    ocr_model: str | None = None
    max_ocr_pages: int = Field(default=30, ge=1, le=500)
    retrieval_top_k: int = Field(default=5, ge=1, le=20)
    # Trace linker bands shared with the frontend: ≥ duplicate is skipped and
    # linked as evidence, between the two a person decides, below is new
    similarity_duplicate_threshold: float = Field(default=0.90, gt=0, le=1)
    similarity_review_threshold: float = Field(default=0.70, gt=0, le=1)
    # Background jobs are not bound by a request timeout; the writers add three calls
    workflow_timeout_seconds: float = Field(default=600.0, gt=0)
    model_max_output_tokens: int = Field(default=8192, ge=256, le=16384)
    # The document, story and diagram writers return long structured output
    artifact_max_output_tokens: int = Field(default=16384, ge=1024, le=65536)
    # Background analysis jobs
    analysis_worker_enabled: bool = True
    analysis_worker_concurrency: int = Field(default=1, ge=1, le=8)
    analysis_worker_poll_seconds: float = Field(default=2.0, gt=0)
    analysis_job_stale_seconds: float = Field(default=900.0, gt=0)

    @property
    def effective_ocr_model(self) -> str:
        return self.ocr_model or self.model_name


@lru_cache
def get_settings() -> Settings:
    return Settings()
