from pathlib import Path
from pydantic_settings import BaseSettings


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # LLM
    llm_model: str = "claude-sonnet-4-6"
    llm_api_key: str = ""

    # Embedding
    embedding_model: str = "BAAI/bge-large-en-v1.5"

    # Reranker
    reranker_model: str = "BAAI/bge-reranker-large"
    reranker_top_k: int = 20

    # Postgres / pgvector (Neon) — vector store + user memory + lead data
    # Field name `postgres_url` maps to env var POSTGRES_URL (pydantic-settings uppercases field names)
    # .env file must contain:  POSTGRES_URL=postgresql://user:pass@host/preventify?sslmode=require
    # Do NOT use DATABASE_URL — that key is not read by this field.
    postgres_url: str = "postgresql://postgres:postgres@localhost:5432/preventify"
    pgvector_collection: str = "preventify_corpus"

    # Ingestion
    corpus_manifest: Path = BASE_DIR / "config" / "corpus_manifest.json"
    extracted_dir: Path = BASE_DIR / "data" / "extracted"
    chunks_dir: Path = BASE_DIR / "data" / "chunks"

    # Risk scoring
    risk_score_timeout_ms: int = 500

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
