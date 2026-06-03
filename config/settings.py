from pathlib import Path
from pydantic_settings import BaseSettings


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Cerebras — model comparison (compare mode only)
    cerebras_api_key: str = ""
    cerebras_base_url: str = "https://api.cerebras.ai/v1"
    cerebras_model: str = "gpt-oss-120b"

    # Chat pipeline LLM models (via OpenRouter)
    phase1_model: str = "google/gemini-2.5-flash-lite"
    phase2_model: str = "google/gemini-2.5-flash"

    # Groq — model comparison tool (tools/model_compare.py)
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # OpenRouter — model comparison (curated fast/mid models from Gemini, GPT, Claude, Grok, DeepSeek)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Gemini — Phase 1/2 runners (current active LLM)
    gemini_api_key: str = ""

    # LLM (legacy Anthropic fields — not used in current pipeline)
    llm_model: str = "claude-sonnet-4-6"
    llm_api_key: str = ""

    # Embedding
    embedding_model: str = "BAAI/bge-large-en-v1.5"

    # Reranker
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L6-v2"
    reranker_top_k: int = 20

    # HuggingFace model cache — must point to D: drive (C: is almost full).
    # Set HF_HOME=D:\hf_cache in .env; run.py applies this before loading any model.
    # All ML model downloads (bge-large-en-v1.5 ~1.3 GB, bge-reranker-v2-m3 ~570 MB)
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
