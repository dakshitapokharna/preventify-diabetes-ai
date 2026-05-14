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

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "preventify_corpus"

    # Postgres (ICMR-NIN food table)
    postgres_url: str = "postgresql://postgres:postgres@localhost:5432/preventify"

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
