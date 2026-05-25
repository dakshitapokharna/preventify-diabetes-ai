"""
embed.py — Loads bge-large-en-v1.5 and encodes text batches.

Model: BAAI/bge-large-en-v1.5
  - 1024-dim dense retrieval model, English-only
  - Correct choice because: Malayalam is translated to English upstream
    before any RAG step. Queries and corpus are both in English at embed time.
  - normalize_embeddings=True: required for cosine similarity in pgvector
  - Do NOT swap to bge-m3 (multilingual) — translation handles language, not the embedder

Reference alignment:
  - guideline_corpus_sources.docx: "Top-k vector retrieval ... reranker on top 20 candidates"
  - base_model_spec.md D2: BAAI/bge-large-en-v1.5
"""

from sentence_transformers import SentenceTransformer
import numpy as np


EXPECTED_DIM = 1024  # bge-large-en-v1.5 — must match vector(1024) in DB schema


def load_model(model_name: str) -> SentenceTransformer:
    """
    Load the embedding model from HuggingFace (cached locally after first download).
    First run will download ~1.3GB. Subsequent runs use the local cache.

    Asserts output dimension == 1024 immediately — catches a wrong model config
    here rather than failing with a cryptic Postgres dimension mismatch error
    after potentially many minutes of embedding work.
    """
    print(f"Loading embedding model: {model_name}")
    # HF_HOME is set to D:\hf_cache in run.py (via .env) before this is called —
    # all downloads land on D:, never C:. See SETUP.md § Step 3.
    model = SentenceTransformer(model_name)
    dim = model.get_sentence_embedding_dimension()
    assert dim == EXPECTED_DIM, (
        f"Wrong embedding model: expected {EXPECTED_DIM}-dim output, "
        f"got {dim}-dim. Check settings.embedding_model — should be BAAI/bge-large-en-v1.5."
    )
    print(f"  Model loaded -- output dim: {dim} OK")
    return model


def embed_texts(model: SentenceTransformer, texts: list[str]) -> np.ndarray:
    """
    Embed a batch of texts. Returns numpy array of shape (len(texts), 1024).

    normalize_embeddings=True ensures vectors are unit-length —
    required for pgvector cosine similarity to work correctly.
    show_progress_bar=False because run.py manages the outer tqdm bar.
    """
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=len(texts),   # caller pre-batches to BATCH_SIZE=32; pass full slice here
    )
    # Cast to float32 explicitly — pgvector adapter requires float32.
    # numpy may return float64 depending on version/platform, which the adapter rejects.
    return vectors.astype(np.float32)
