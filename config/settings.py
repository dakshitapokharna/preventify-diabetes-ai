from pathlib import Path
from pydantic_settings import BaseSettings


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Cerebras — all LLM calls (Phase 1, Phase 2, memory compressor) for testing.
    # Single model for everything: gpt-oss-120b (free tier)
    # Runners read CEREBRAS_API_KEY directly from os.environ.
    # Switch back to OpenRouter (Gemini) after base model clinical sign-off.
    cerebras_api_key: str = ""

    # Cerebras base URL (OpenAI-compatible)
    cerebras_base_url: str = "https://api.cerebras.ai/v1"

    # LLM model — single model for all phases during testing
    cerebras_model: str = "gpt-oss-120b"

    # LLM (legacy Anthropic fields — not used in current pipeline)
    llm_model: str = "claude-sonnet-4-6"
    llm_api_key: str = ""

    # Embedding
    embedding_model: str = "BAAI/bge-large-en-v1.5"

    # Reranker
    reranker_model: str = "BAAI/bge-reranker-large"
    reranker_top_k: int = 20

    # HuggingFace model cache — must point to D: drive (C: is almost full).
    # Set HF_HOME=D:\hf_cache in .env; run.py applies this before loading any model.
    # All ML model downloads (bge-large-en-v1.5 ~1.3 GB, bge-reranker-large ~1.1 GB)
    # land here, never on C:.
    hf_home: str = r"D:\hf_cache"

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
