"""
engine/phase2_runner.py — Phase 2 RAG Pipeline

This is the Phase 2 equivalent of engine/phase1_runner.py.

Called when Phase 1 returns context_sufficient=True and intent != "escalation_only".
Runs the full retrieval-augmented generation pipeline and returns the patient-facing
clinical education response.

Pipeline steps (in order):
  1. Guard — skip if escalation_only or Phase 1 fallback with no profile
  2. build_phase2_query()       — enrich retrieval query (query_builder.py)
  3. resolve_condition_flags()  — combine message signals + stored profile flags
  4. build_retrieval_filter()   — Tier 1 only vs Tier 1 + triggered Tier 2
  5. Check query_cache table    — skip ANN search if recent identical query exists
  6. Embed enriched query       — bge-large-en-v1.5 via thread pool (sync → async)
  7. pgvector ANN search        — top-20 candidates via asyncpg
  8. Write query_cache          — save top-20 chunk_ids for 24 hours
  9. Rerank top-20 → top-5      — bge-reranker-v2-m3 via thread pool (ONNX INT8 if available)
  10. Sort by grade_priority    — strongest evidence first in the Gemini prompt
  11. format_chunks_for_prompt() — build <clinical_context> block
  12. Build Gemini 2.5 Pro prompt — system cache + short_memory + turns + chunks + message
  13. Generate response          — gemini-2.5-pro-preview-06-05 with context caching
  14. check_constraints()       — scan for dose/diagnosis violations
  15. Return result dict

Usage:
    from engine.phase2_runner import run_phase2, load_reranker

    # At startup (load once, pass to every run_phase2 call):
    embedder = load_model(settings.embedding_model)   # from ingestion/embedder/embed.py
    reranker = load_reranker(settings.reranker_model)

    result = await run_phase2(
        current_message="Can I eat rice?",
        session_turns=[{"role": "patient", "content": "..."}, ...],
        phase1_output=validated_phase1_output,
        profile={"condition_flags": ["ckd"], "medications_mentioned": ["metformin"], ...},
        short_memory="Patient: Rajan, 58\\nCondition: T2DM\\nFlags: CKD",
        db_conn=asyncpg_connection,    # must have register_vector() called on it
        embedder=embedder,
        reranker=reranker,
        user_id="uuid-or-whatsapp-number",
    )

    # result["text"]                   → patient-facing English response
    # result["chunks_used"]            → list of chunk_id strings (for SaMD audit trail)
    # result["condition_flags_active"] → set → list of flags that opened Tier 2
    # result["query_cache_hit"]        → True if ANN search was skipped
    # result["reranker_scores"]        → top-5 scores (0–1, for ops monitoring)
    # result["_fallback"]              → True if pipeline failed, False on success
    # result["_fallback_reason"]       → error type string if _fallback is True

Environment:
    OPENROUTER_API_KEY — required. Set in .env at project root.

LLM routing:
    All generation calls go through OpenRouter (https://openrouter.ai/api/v1) using the
    OpenAI-compatible chat completions endpoint. The model is "google/gemini-2.5-pro-preview-06-05".
    Context caching is not available through OpenRouter — the system prompt is sent on every call.

Failure handling (mirrors phase1_runner.py):
    Any error at any step returns PHASE2_FALLBACK — never raises, never blocks the patient.
    All failures are logged to logs/phase2_failures.jsonl.
    Constraint violations are logged with the full generated text (first 2,000 chars).
"""

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import time

import numpy as np
import openai

from engine.query_builder import build_phase2_query
from schemas.phase2_schema import (
    PHASE2_FALLBACK,
    PHASE2_CONSTRAINT_FALLBACK_TEXT,
    build_retrieval_filter,
    check_constraints,
    format_chunks_for_prompt,
    resolve_condition_flags,
)

log = logging.getLogger(__name__)


def _apply_kerala_food_filter(text: str) -> str:
    """
    Replace non-Kerala food terms with Kerala equivalents in generated responses.
    Catches models that ignore the system prompt FORBIDDEN list.
    """
    # North Indian fried snacks → Kerala fried snacks
    text = re.sub(r'\b(samosas?|kachoris?)\b', 'banana chips (ethakka upperi)', text, flags=re.IGNORECASE)
    text = re.sub(r'\bpav\s+bhaji\b', 'puttu with kadala', text, flags=re.IGNORECASE)
    text = re.sub(r'\bchaat\b', 'roasted kadala', text, flags=re.IGNORECASE)
    text = re.sub(r'\bbhel\s+puri\b', 'roasted kadala', text, flags=re.IGNORECASE)
    text = re.sub(r'\bvada\s+pav\b', 'puttu', text, flags=re.IGNORECASE)

    # North Indian dairy / protein → Kerala protein
    text = re.sub(r'\bpaneer\b', 'kadala (brown chickpeas)', text, flags=re.IGNORECASE)
    text = re.sub(r'\btofu\b', 'kadala', text, flags=re.IGNORECASE)
    text = re.sub(r'\bcottage\s+cheese\b', 'curd (thayiru)', text, flags=re.IGNORECASE)
    text = re.sub(r'\bsoy\s+(milk|chunks|protein)\b', 'kadala or cherupayar', text, flags=re.IGNORECASE)

    # North Indian breads → Kerala staples
    text = re.sub(r'\b(rotis?|chapatis?|parathas?|naans?)\b', 'rice or puttu', text, flags=re.IGNORECASE)

    # Western grains / cereals → Kerala staples
    text = re.sub(r'\b(pasta|noodles)\b', 'rice', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(oatmeal|oats|granola|muesli)\b', 'puttu', text, flags=re.IGNORECASE)
    text = re.sub(r'\bbrown\s+rice\b', 'matta rice', text, flags=re.IGNORECASE)
    text = re.sub(r'\bwhole[\s-]wheat\s+(bread|roti)\b', 'puttu or idli', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(multigrain|whole[\s-]grain)\s+(bread|roti|crackers?|foods?|cereals?|products?)\b', 'matta rice or puttu', text, flags=re.IGNORECASE)
    text = re.sub(r'\bwhole[\s-]grains?\b', 'matta rice, kadala, and cherupayar', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(bread|toast)\b(?!\s+fruit|\s+and\s+butter)', 'idli', text, flags=re.IGNORECASE)

    # Western snacks / packaged → Kerala snacks
    text = re.sub(r'\b(crackers?|rice\s+cakes?)\b', 'roasted kadala', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(granola|energy|protein)\s+bars?\b', 'roasted kadala', text, flags=re.IGNORECASE)
    text = re.sub(r'\bdigestive\s+biscuits?\b', 'roasted kadala', text, flags=re.IGNORECASE)

    # Western dairy / drinks → Kerala equivalents
    text = re.sub(r'\bcanned\s+soups?\b', 'packaged foods', text, flags=re.IGNORECASE)
    text = re.sub(r'\bgreek\s+yogurt\b', 'curd (thayiru)', text, flags=re.IGNORECASE)
    text = re.sub(r'\blow[‑-]fat\s+yogurt\b', 'curd (thayiru)', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(almond|oat|soy)\s+milk\b', 'tender coconut water (ilaneer)', text, flags=re.IGNORECASE)

    # Western "healthy" foods with no Kerala equivalent
    text = re.sub(r'\bdark\s+chocolate\b', 'a small piece of ripe nendran banana', text, flags=re.IGNORECASE)

    # Sick day western foods → Kerala sick-day food
    text = re.sub(r'\bchicken\s+soup\b', 'kanji (rice gruel)', text, flags=re.IGNORECASE)

    return text


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

MODEL_ID         = "google/gemini-2.5-flash"
OPENROUTER_URL   = "https://openrouter.ai/api/v1"
# prompt version: kerala-v4 — expanded forbidden list + new trap categories covered
REQUEST_TIMEOUT  = 60.0           # Phase 2 is slower — Gemini Flash via OpenRouter ~5–15s
RETRY_SLEEP      = 2.0
TOP_K_ANN       = 5               # reduced from 20 → CPU reranker was taking 73–204s; 5 candidates ~10s on CPU
TOP_K_FINAL     = 5               # reranker output sent to LLM (keep same — all 5 ANN candidates go to LLM)
CACHE_TTL_HOURS = 24              # query_cache table TTL (hours)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "phase2_system_prompt.txt"
LOG_PATH    = Path(__file__).parent.parent / "logs"    / "phase2_failures.jsonl"

# Thread pool for running sync ML models (embedder + reranker) in async context
# max_workers=2: one slot for embedder, one for reranker (they never run simultaneously)
_thread_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="phase2_ml")

# ─────────────────────────────────────────────────────────────────────────────
# Module-level state
# ─────────────────────────────────────────────────────────────────────────────

_system_prompt_text: Optional[str] = None


def _reset_module_state() -> None:
    """Reset module-level state. Called by tests between test cases."""
    global _system_prompt_text
    _system_prompt_text = None


# ─────────────────────────────────────────────────────────────────────────────
# Reranker loader
#
# Separate from the embedder (loaded in ingestion/embedder/embed.py) because
# reranking uses a cross-encoder, not a bi-encoder.
#
# sentence-transformers CrossEncoder is synchronous — always called via _rerank_async()
# below which runs it in the thread pool.
# Note: FlagEmbedding FlagReranker has a compatibility bug with transformers 5.x
# (XLMRobertaTokenizer.prepare_for_model removed). CrossEncoder from sentence-transformers
# uses the BAAI/bge-reranker-v2-m3 weights and is fully compatible.
# ─────────────────────────────────────────────────────────────────────────────

_ONNX_INT8_DIR = Path("D:/hf_cache/reranker_onnx_int8")


def load_reranker(model_name: str):
    """
    Load the reranker for CPU inference.

    Priority order:
      1. ONNX INT8 quantized model (D:/hf_cache/reranker_onnx_int8) — ~3-5x faster on CPU.
         Run scripts/quantize_reranker.py once to produce this artifact.
      2. sentence-transformers CrossEncoder fallback — works out of the box, slower.

    Args:
        model_name: e.g. "BAAI/bge-reranker-v2-m3" (from settings.reranker_model)

    Returns:
        dict with keys "type" ("onnx" | "crossencoder"), "model", "tokenizer" (ONNX only).
        _rerank_async() understands both shapes.
    """
    # ── ONNX path (fast) ──────────────────────────────────────────────────────
    if _ONNX_INT8_DIR.exists():
        try:
            from optimum.onnxruntime import ORTModelForSequenceClassification
            from transformers import AutoTokenizer

            log.info("phase2_runner: loading ONNX INT8 reranker from %s", _ONNX_INT8_DIR)
            ort_model = ORTModelForSequenceClassification.from_pretrained(
                str(_ONNX_INT8_DIR), file_name="model_quantized.onnx"
            )
            # Tokenizer lives in HF hub cache, not in the quantized artifact dir
            tokenizer = AutoTokenizer.from_pretrained(model_name)
            log.info("phase2_runner: ONNX reranker loaded OK")
            return {"type": "onnx", "model": ort_model, "tokenizer": tokenizer}
        except Exception as exc:
            log.warning("phase2_runner: ONNX load failed (%s) — falling back to CrossEncoder", exc)

    # ── CrossEncoder fallback (slower) ───────────────────────────────────────
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise ImportError(
            "sentence-transformers is not installed. Run: pip install sentence-transformers"
        ) from exc

    log.info("phase2_runner: loading CrossEncoder reranker: %s", model_name)
    reranker = CrossEncoder(model_name)
    log.info("phase2_runner: CrossEncoder reranker loaded OK")
    return {"type": "crossencoder", "model": reranker, "tokenizer": None}


# ─────────────────────────────────────────────────────────────────────────────
# Async wrappers for sync ML operations
# ─────────────────────────────────────────────────────────────────────────────

async def _embed_async(embedder, text: str) -> np.ndarray:
    """
    Run bge-large-en-v1.5 embedding in the thread pool.

    The SentenceTransformer model is synchronous (PyTorch). Running it directly
    in an async function would block the event loop. This wrapper uses the
    module-level thread pool to keep the async pipeline responsive.

    Returns:
        numpy array of shape (1024,), dtype float32, unit-normalized.
    """
    loop = asyncio.get_event_loop()
    embedding = await loop.run_in_executor(
        _thread_pool,
        lambda: embedder.encode(
            [text],
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0].astype(np.float32),
    )
    return embedding


def _rerank_sync(reranker_handle: dict, query: str, chunks: list) -> list:
    """
    Synchronous reranking — called inside the thread pool by _rerank_async.

    Handles both the ONNX and CrossEncoder shapes returned by load_reranker().
    Both paths return raw logits → sigmoid → 0-1 scores.
    """
    import torch

    texts = [chunk["text"] for chunk in chunks]

    if reranker_handle["type"] == "onnx":
        tokenizer = reranker_handle["tokenizer"]
        model = reranker_handle["model"]
        pairs = [[query, t] for t in texts]
        inputs = tokenizer(
            pairs,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        with torch.no_grad():
            logits = model(**inputs).logits.squeeze(-1)
        return torch.sigmoid(logits).tolist()

    # CrossEncoder fallback
    model = reranker_handle["model"]
    pairs = [(query, t) for t in texts]
    raw = model.predict(pairs, apply_softmax=True)
    if hasattr(raw, "tolist"):
        return raw.tolist()
    return list(raw) if not isinstance(raw, float) else [raw]


async def _rerank_async(reranker, query: str, chunks: list) -> list:
    """
    Run reranker scoring in the thread pool (keeps async event loop unblocked).

    Args:
        reranker: dict returned by load_reranker() — type "onnx" or "crossencoder".
        query:    Enriched retrieval query (NOT the patient-facing message).
        chunks:   List of chunk dicts from pgvector — each must have "text" key.

    Returns:
        List of float scores 0.0–1.0, one per chunk, same order as input.
    """
    loop = asyncio.get_event_loop()
    scores = await loop.run_in_executor(
        _thread_pool,
        lambda: _rerank_sync(reranker, query, chunks),
    )
    return scores


# ─────────────────────────────────────────────────────────────────────────────
# pgvector ANN search
# ─────────────────────────────────────────────────────────────────────────────

async def _pgvector_search(
    conn,
    embedding: np.ndarray,
    tier_filter,
    trigger_filter: Optional[list],
    top_k: int = TOP_K_ANN,
) -> list:
    """
    Run pgvector ANN search on preventify_corpus.

    Two SQL paths depending on whether condition flags are active:
      Path 1 (no flags):   WHERE retrieval_tier = 'core'
      Path 2 (with flags): WHERE retrieval_tier = ANY($tiers)
                           AND (condition_trigger IS NULL OR condition_trigger = ANY($flags))

    The `<=>` operator is cosine distance (lower = more similar).
    asyncpg + pgvector adapter handles numpy array → vector conversion.

    Args:
        conn:           asyncpg Connection with register_vector() already called.
        embedding:      Query embedding, shape (1024,), dtype float32.
        tier_filter:    "core" (no flags) or ["core", "triggered"] (with flags).
        trigger_filter: None (no flags) or list of flag strings.
        top_k:          Number of candidates to return (default: TOP_K_ANN=20).

    Returns:
        List of dicts — one per row — with all chunk fields needed for reranking
        and Gemini prompt construction.
    """
    if trigger_filter is None:
        # Tier 1 only — no condition flags active
        rows = await conn.fetch(
            """
            SELECT
                chunk_id, source, year, section_title, text,
                retrieval_tier, grade_priority, safety_critical,
                embedding <=> $1 AS distance
            FROM preventify_corpus
            WHERE retrieval_tier = 'core'
            ORDER BY distance ASC
            LIMIT $2
            """,
            embedding,
            top_k,
        )
    else:
        # Tier 1 + triggered Tier 2 — condition flags opened additional sources
        # condition_trigger IS NULL covers all Tier 1 chunks (they have no trigger)
        # condition_trigger = ANY($2) covers only the Tier 2 chunks for active flags
        rows = await conn.fetch(
            """
            SELECT
                chunk_id, source, year, section_title, text,
                retrieval_tier, grade_priority, safety_critical,
                embedding <=> $1 AS distance
            FROM preventify_corpus
            WHERE retrieval_tier = ANY($2)
              AND (condition_trigger IS NULL OR condition_trigger = ANY($3))
            ORDER BY distance ASC
            LIMIT $4
            """,
            embedding,
            tier_filter,       # ["core", "triggered"]
            trigger_filter,    # ["ckd"] or ["ckd", "cardio"] etc.
            top_k,
        )

    # asyncpg returns Record objects — convert to plain dicts for downstream use
    return [dict(row) for row in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Query cache (application-layer, in Neon PostgreSQL)
#
# The most common patient queries (rice portions, HbA1c explanation, chaaya,
# insulin fear) will produce the same pgvector top-20 results across patients.
# Cache the chunk_ids to skip the ANN search (vector distance over 4,059 rows)
# on subsequent identical queries.
#
# Cache key: SHA256[:32] of the enriched query string
# Cache row schema: see schemas/users_table.sql (query_cache table)
# TTL: 24 hours (hardcoded at write time; expire checked at read time)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_query_hash(enriched_query: str) -> str:
    """SHA256[:32] of enriched query — same algorithm as text_hash in chunks."""
    return hashlib.sha256(enriched_query.encode("utf-8")).hexdigest()[:32]


async def _check_query_cache(conn, query_hash: str) -> Optional[list]:
    """
    Return cached chunk_ids if a non-expired entry exists, else None.
    Increments hit_count on cache hit.
    """
    try:
        row = await conn.fetchrow(
            """
            SELECT chunk_ids FROM query_cache
            WHERE query_hash = $1 AND expires_at > NOW()
            """,
            query_hash,
        )
        if row is None:
            return None

        # Increment hit count (best-effort — don't fail the pipeline on this)
        await conn.execute(
            "UPDATE query_cache SET hit_count = hit_count + 1 WHERE query_hash = $1",
            query_hash,
        )
        return list(row["chunk_ids"])

    except Exception as exc:
        log.warning("phase2_runner: query_cache read failed (%s) — proceeding without cache", exc)
        return None


async def _write_query_cache(
    conn,
    query_hash: str,
    chunk_ids: list,
    active_flags: set,
) -> None:
    """
    Write top-20 chunk_ids to query_cache with 24-hour TTL.
    ON CONFLICT DO UPDATE — replaces stale entries for the same hash.
    Best-effort — failure is logged and silently swallowed.
    """
    try:
        await conn.execute(
            """
            INSERT INTO query_cache (query_hash, chunk_ids, condition_flags, expires_at)
            VALUES ($1, $2, $3, NOW() + INTERVAL '24 hours')
            ON CONFLICT (query_hash) DO UPDATE
                SET chunk_ids       = EXCLUDED.chunk_ids,
                    condition_flags = EXCLUDED.condition_flags,
                    hit_count       = query_cache.hit_count + 1,
                    cached_at       = NOW(),
                    expires_at      = EXCLUDED.expires_at
            """,
            query_hash,
            chunk_ids,
            list(active_flags),
        )
    except Exception as exc:
        log.warning("phase2_runner: query_cache write failed (%s) — continuing", exc)


async def _fetch_chunks_by_ids(conn, chunk_ids: list) -> list:
    """
    Fetch full chunk rows from preventify_corpus by chunk_id list.
    Used when query_cache returns a cache hit — ANN search is skipped.
    Returns rows in the same order as chunk_ids (preserves ANN ranking).
    """
    if not chunk_ids:
        return []

    rows = await conn.fetch(
        """
        SELECT chunk_id, source, year, section_title, text,
               retrieval_tier, grade_priority, safety_critical
        FROM preventify_corpus
        WHERE chunk_id = ANY($1)
        """,
        chunk_ids,
    )

    # Restore ANN order (DB rows may come back in any order)
    row_by_id = {row["chunk_id"]: dict(row) for row in rows}
    return [row_by_id[cid] for cid in chunk_ids if cid in row_by_id]


# ─────────────────────────────────────────────────────────────────────────────
# Gemini 2.5 Pro context caching + prompt building
# ─────────────────────────────────────────────────────────────────────────────

def _load_system_prompt() -> str:
    """Load Phase 2 system prompt from disk. Cached in module state after first read."""
    global _system_prompt_text
    if _system_prompt_text is None:
        _system_prompt_text = PROMPT_PATH.read_text(encoding="utf-8")
        token_estimate = len(_system_prompt_text) // 4
        log.debug(
            "phase2_runner: system prompt loaded (~%d tokens)", token_estimate,
        )
    return _system_prompt_text


def _build_openai_messages(
    current_message: str,
    session_turns: list,
    short_memory: str,
    chunk_context_block: str,
    system_prompt: str,
    intent: Optional[str] = None,
    qds_score: Optional[int] = None,
    session_context: Optional[str] = None,
    location_hint: Optional[str] = None,
) -> list:
    """
    Build the OpenAI-format messages list for the Phase 2 generation call.

    Layout (in order):
        1. system — Phase 2 system prompt (role, safety rules, routing guides)
        2. user   — Phase 1 context block + patient memory + clinical context (injected)
        3. assistant — acknowledgement placeholder (keeps user/assistant alternating)
        4. user/assistant — session history (last 5 prior turns)
        5. user   — current patient message (always last)

    Design:
        - System prompt is sent on every call (no context caching via OpenRouter).
        - Phase 1 context (intent, qds_score, session_context) is injected as the
          first user message so the model reads framing before session history.
        - intent=general_dsmes is omitted (default routing — no special behaviour).
        - session_context="self" is omitted (default — no family framing needed).
        - The enriched query served retrieval only — only the original patient message
          is sent to the model.

    Args:
        current_message:     Original patient message (NOT the enriched query).
        session_turns:       Prior turns as [{"role": "patient"/"bot", "content": "..."}].
                             Does NOT include the current message.
        short_memory:        ~100-token compressed patient profile string.
                             Empty string "" for new users (no profile yet).
        chunk_context_block: Formatted <clinical_context> block from format_chunks_for_prompt().
        system_prompt:       Phase 2 system prompt text (loaded from disk once).
        intent:              Phase 1 intent string (e.g. "taking_medication").
                             None or "general_dsmes" → not included (default behaviour).
        qds_score:           Phase 1 QDS score 1–5. Included when not None.
        session_context:     Phase 1 session_context. Included only when "family_member_inquiry".

    Returns:
        List of {"role": ..., "content": ...} dicts for the chat completions API.
    """
    messages = [{"role": "system", "content": system_prompt}]

    # Opening user turn — inject phase1_context + patient memory + clinical context
    opening_parts = []

    # ── Phase 1 context block ─────────────────────────────────────────────────
    phase1_lines = []
    if intent and intent != "general_dsmes":
        phase1_lines.append(f"Question intent: {intent}")
    if qds_score is not None:
        phase1_lines.append(f"Question depth: QDS {qds_score}")
    if session_context == "family_member_inquiry":
        phase1_lines.append(
            "Conversation context: This person is asking about a family member's diabetes, "
            "not their own condition. Frame the response for a family caregiver."
        )
    if location_hint:
        phase1_lines.append(
            f"Patient location: {location_hint} — use local food names, portion units (ladles not cups), "
            "and seasonal/cultural context relevant to this region."
        )
    if phase1_lines:
        opening_parts.append("<phase1_context>\n" + "\n".join(phase1_lines) + "\n</phase1_context>")

    # ── Patient memory ────────────────────────────────────────────────────────
    if short_memory:
        opening_parts.append(f"<patient_memory>\n{short_memory}\n</patient_memory>")
    opening_parts.append(chunk_context_block)

    messages.append({"role": "user", "content": "\n\n".join(opening_parts)})

    # Placeholder assistant turn — keeps user/assistant alternating before history
    messages.append({
        "role": "assistant",
        "content": (
            "Understood. I have read the patient's profile and the clinical evidence. "
            "Please share the conversation."
        ),
    })

    # Session history — last 5 prior turns
    for turn in session_turns[-5:]:
        role = "assistant" if turn.get("role") == "bot" else "user"
        messages.append({"role": role, "content": turn.get("content", "")})

    # Current patient message — always the final user turn
    messages.append({"role": "user", "content": current_message})

    return messages


# ─────────────────────────────────────────────────────────────────────────────
# Failure logging
# ─────────────────────────────────────────────────────────────────────────────

def _log_failure(
    current_message: str,
    user_id: Optional[str],
    error_type: str,
    raw_output: str,
    attempt_count: int,
    extra: Optional[dict] = None,
) -> None:
    """
    Append a Phase 2 failure record to logs/phase2_failures.jsonl.

    Schema:
        timestamp, user_id, message_id, error_type, raw_output (first 2,000 chars),
        fallback_applied (always True), attempt_count, extra (constraint violations etc.)

    Ops review: Phase 2 failure rate and constraint violation patterns should be
    reviewed weekly. High constraint violation rate suggests system prompt needs tightening.
    High API failure rate suggests model stability issue.
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "user_id":          user_id or "unknown",
        "message_id":       str(uuid.uuid4()),
        "error_type":       error_type,
        "raw_output":       raw_output[:2000],
        "fallback_applied": True,
        "attempt_count":    attempt_count,
        "extra":            extra or {},
    }
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as log_exc:
        log.error("phase2_runner: could not write to failure log: %s", log_exc)


def _make_fallback(error_type: str, fallback_text: Optional[str] = None) -> dict:
    """
    Return a deep copy of PHASE2_FALLBACK with the error type stamped in.
    Optionally override the text (used for constraint violations which have different text).
    """
    fb = copy.deepcopy(PHASE2_FALLBACK)
    fb["_fallback"] = True
    fb["_fallback_reason"] = error_type
    if fallback_text:
        fb["text"] = fallback_text
    return fb


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

async def run_phase2(
    current_message: str,
    session_turns: list,
    phase1_output: dict,
    profile: Optional[dict],
    short_memory: str,
    db_conn,
    embedder,
    reranker,
    user_id: Optional[str] = None,
    max_retries: int = 1,
) -> dict:
    """
    Run Phase 2 RAG pipeline for one patient message.

    Always returns a valid response dict — never raises.

    On success:    result["_fallback"] == False
    On any error:  result["_fallback"] == True
                   result["_fallback_reason"] == "<error_type>"

    Error type strings:
        "escalation_bypass"          intent=escalation_only — Phase 2 skipped intentionally
        "missing_api_key"            OPENROUTER_API_KEY not set
        "no_chunks_retrieved"        pgvector returned 0 results
        "embed_error:*"              embedding model failed
        "rerank_error:*"             reranker failed
        "timeout"                    Gemini did not respond within REQUEST_TIMEOUT
        "rate_limit_429"             HTTP 429 — retried once, still failed
        "service_unavailable_503"    HTTP 503
        "json_decode_error:*"        unexpected — Phase 2 returns free text
        "api_error:*"                other API error
        "response_text_error:*"      response.text accessor threw
        "constraint_violation"       generated text failed safety constraint check

    Args:
        current_message:  Patient's current message (English, post-translation).
                          Must be the ORIGINAL message — not the enriched query.
        session_turns:    Prior turns as [{"role": "patient"/"bot", "content": "..."}].
                          Does NOT include current_message. Max 5 used.
        phase1_output:    Validated Phase 1 output dict from validate_phase1_output().
                          Used for: mid_clarification_resolved, profile_signals.
        profile:          User profile dict from DB. Keys used:
                            condition_flags, medications_mentioned, diabetes_type, short_memory.
                          None for new users (first message, no DB record yet).
        short_memory:     ~100-token compressed patient profile string.
                          Empty string "" for new users.
        db_conn:          asyncpg Connection. Must have register_vector() called on it.
        embedder:         SentenceTransformer (bge-large-en-v1.5). Load with embed.load_model().
        reranker:         dict from load_reranker() — ONNX INT8 or CrossEncoder fallback.
        user_id:          Patient identifier for failure log. Optional.
        max_retries:      Retry attempts for recoverable errors. Default 1.

    Returns:
        Dict with keys:
            text                    — patient-facing English response
            chunks_used             — list[str] of chunk_id values used (SaMD audit trail)
            condition_flags_active  — list[str] of flags that opened Tier 2 sources
            query_cache_hit         — bool, True if ANN search was skipped via cache
            reranker_scores         — list[float] of top-5 scores (for ops monitoring)
            _fallback               — bool
            _fallback_reason        — str
    """
    # ── Guard 1: API key ───────────────────────────────────────────────────────
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        log.error("phase2_runner: OPENROUTER_API_KEY not set — returning fallback immediately")
        _log_failure(current_message, user_id, "missing_api_key", "", 0)
        return _make_fallback("missing_api_key")

    # ── Guard 2: escalation_only bypass ───────────────────────────────────────
    # If Phase 1 classified the intent as escalation_only, Phase 2 must not run.
    # The Response Formatter + Risk Engine handle the emergency response directly.
    # This guard is a safety net — the caller (session manager) should also check.
    if phase1_output.get("intent") == "escalation_only":
        log.debug("phase2_runner: escalation_only — bypassing Phase 2")
        fb = _make_fallback("escalation_bypass")
        fb["_fallback"] = False   # not an error — intentional bypass
        return fb

    _t0 = time.perf_counter()
    _t  = {}   # step → elapsed_ms, filled as each step completes

    # ─────────────────────────────────────────────────────────────────────────
    # Step 1 — Build enriched retrieval query
    # ─────────────────────────────────────────────────────────────────────────
    _ts = time.perf_counter()
    enriched_query = build_phase2_query(
        current_message=current_message,
        session_turns=session_turns,
        profile=profile,
        mid_clarification_resolved=phase1_output.get("mid_clarification_resolved", False),
    )
    log.debug("phase2_runner: enriched_query=%r", enriched_query[:120])

    # ─────────────────────────────────────────────────────────────────────────
    # Step 2 — Resolve condition flags
    # Merge: current message keywords + stored profile flags (permanent)
    # ─────────────────────────────────────────────────────────────────────────
    stored_flags = (profile or {}).get("condition_flags", [])
    # Also include any flags Phase 1 just detected in this message
    phase1_flags = phase1_output.get("profile_signals", {}).get("condition_flags", [])
    active_flags = resolve_condition_flags(
        message=current_message,
        stored_flags=list(set((stored_flags or []) + (phase1_flags or []))),
    )
    log.debug("phase2_runner: active_flags=%s", active_flags)

    # ─────────────────────────────────────────────────────────────────────────
    # Step 3 — Build retrieval filter
    # ─────────────────────────────────────────────────────────────────────────
    tier_filter, trigger_filter = build_retrieval_filter(active_flags)
    _t["query_build_ms"] = round((time.perf_counter() - _ts) * 1000)

    # ─────────────────────────────────────────────────────────────────────────
    # Steps 4–8 — Retrieve top-20 chunks (cache-first)
    # ─────────────────────────────────────────────────────────────────────────
    query_hash       = _compute_query_hash(enriched_query)
    query_cache_hit  = False
    top_20_chunks: list = []

    # Step 4 — Check query_cache
    _ts = time.perf_counter()
    cached_chunk_ids = await _check_query_cache(db_conn, query_hash)

    if cached_chunk_ids:
        # Cache hit — fetch chunk data by ID, skip ANN search
        log.debug("phase2_runner: query_cache HIT — skipping ANN search (%s)", query_hash[:12])
        top_20_chunks   = await _fetch_chunks_by_ids(db_conn, cached_chunk_ids)
        query_cache_hit = True
        _t["cache_check_ms"] = round((time.perf_counter() - _ts) * 1000)
        _t["embed_ms"]       = 0
        _t["ann_search_ms"]  = 0

    else:
        _t["cache_check_ms"] = round((time.perf_counter() - _ts) * 1000)

        # Cache miss — embed query then run ANN search
        # Step 5 — Embed enriched query
        _ts = time.perf_counter()
        try:
            embedding = await _embed_async(embedder, enriched_query)
        except Exception as exc:
            error_type = f"embed_error:{type(exc).__name__}"
            log.error("phase2_runner: embedding failed: %s", exc)
            _log_failure(current_message, user_id, error_type, "", 1)
            return _make_fallback(error_type)
        _t["embed_ms"] = round((time.perf_counter() - _ts) * 1000)

        # Step 6 — pgvector ANN search
        _ts = time.perf_counter()
        try:
            top_20_chunks = await _pgvector_search(
                db_conn, embedding, tier_filter, trigger_filter, top_k=TOP_K_ANN
            )
        except Exception as exc:
            error_type = f"pgvector_error:{type(exc).__name__}"
            log.error("phase2_runner: pgvector search failed: %s", exc)
            _log_failure(current_message, user_id, error_type, "", 1)
            return _make_fallback(error_type)
        _t["ann_search_ms"] = round((time.perf_counter() - _ts) * 1000)

        # Step 7 — Write query_cache (best-effort, non-blocking)
        if top_20_chunks:
            await _write_query_cache(
                db_conn,
                query_hash,
                [c["chunk_id"] for c in top_20_chunks],
                active_flags,
            )

    if not top_20_chunks:
        # Do NOT return fallback. format_chunks_for_prompt([]) returns:
        #   "No clinical evidence was retrieved for this query.
        #    Answer only from general DSMES educator knowledge. If uncertain, say so."
        # This is correct for QDS 1–2 general questions (e.g. "What is HbA1c?").
        # Returning the generic "I'm having trouble" fallback is wrong — it blocks
        # answers that Gemini 2.5 Pro can give from general educator knowledge.
        # Log as an ops signal: empty retrieval should be rare once embedder is run.
        log.warning(
            "phase2_runner: no chunks retrieved for query=%r — proceeding with "
            "general DSMES educator knowledge only (is embedder populated?)",
            enriched_query[:80],
        )
        _log_failure(
            current_message, user_id, "no_chunks_retrieved_general_only", "", 0
        )
        # Skip reranking entirely — jump to format + generation with empty list
        top_5_chunks = []
        top_5_scores = []
        log.debug("phase2_runner: skipping reranker (no chunks to rank)")

    # ─────────────────────────────────────────────────────────────────────────
    # Step 8 — Rerank top-20 → top-5 (skipped when no chunks retrieved)
    # bge-reranker-large cross-encoder: reads (query, chunk) together as a pair.
    # More precise than embedder cosine similarity but slower — runs on top-20 only.
    # When top_20_chunks is empty (no_chunks_retrieved path), top_5_chunks and
    # top_5_scores are already set to [] above — skip reranking entirely.
    # ─────────────────────────────────────────────────────────────────────────
    if top_20_chunks:
        _ts = time.perf_counter()
        try:
            scores = await _rerank_async(reranker, enriched_query, top_20_chunks)
        except Exception as exc:
            error_type = f"rerank_error:{type(exc).__name__}"
            log.error("phase2_runner: reranker failed: %s", exc)
            # Degraded mode: use top-5 from ANN order (no reranking) rather than blocking
            log.warning("phase2_runner: using ANN order as fallback (no reranker scores)")
            scores = [1.0] * len(top_20_chunks)  # synthetic equal scores
        _t["rerank_ms"] = round((time.perf_counter() - _ts) * 1000)

        # Sort by score descending, take top-5
        scored_chunks = sorted(zip(scores, top_20_chunks), key=lambda x: x[0], reverse=True)
        top_5_pairs   = scored_chunks[:TOP_K_FINAL]
        top_5_scores  = [round(s, 4) for s, _ in top_5_pairs]
        top_5_chunks  = [chunk for _, chunk in top_5_pairs]

        # Step 9 — Sort top-5 by grade_priority ascending (grade 1 = strongest → shown first)
        # This means Gemini 2.5 Pro reads the highest-quality evidence first.
        top_5_chunks.sort(key=lambda c: c.get("grade_priority", 5))

    log.debug(
        "phase2_runner: top-5 sources=%s scores=%s",
        [c.get("source") for c in top_5_chunks],
        top_5_scores,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Step 10 — Format chunks for Gemini prompt
    # ─────────────────────────────────────────────────────────────────────────
    chunk_context_block = format_chunks_for_prompt(top_5_chunks)

    # ─────────────────────────────────────────────────────────────────────────
    # Step 11 — Build Gemini contents list
    # Extract Phase 1 framing signals to pass into Gemini for response calibration.
    # ─────────────────────────────────────────────────────────────────────────
    p1_intent          = phase1_output.get("intent")
    p1_qds_score       = phase1_output.get("qds_score")
    p1_session_context = phase1_output.get("profile_signals", {}).get("session_context")
    p1_location_hint   = (profile or {}).get("location_hint") or "Kerala"

    system_prompt = _load_system_prompt()
    messages = _build_openai_messages(
        current_message=current_message,
        session_turns=session_turns,
        short_memory=short_memory,
        chunk_context_block=chunk_context_block,
        system_prompt=system_prompt,
        intent=p1_intent,
        qds_score=p1_qds_score,
        session_context=p1_session_context,
        location_hint=p1_location_hint,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Steps 12–13 — Gemini 2.5 Pro generation via OpenRouter
    # ─────────────────────────────────────────────────────────────────────────
    client = openai.AsyncOpenAI(
        base_url=OPENROUTER_URL,
        api_key=api_key,
        default_headers={
            "HTTP-Referer": "https://preventify.in",
            "X-Title":      "Preventify Diabetes Educator",
        },
    )

    last_error_type = "unknown"
    last_raw        = ""
    _t.setdefault("rerank_ms", 0)   # skipped when no chunks

    # ── Retry loop (mirrors phase1_runner.py) ─────────────────────────────────
    _ts_llm = time.perf_counter()
    for attempt in range(max_retries + 1):
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=MODEL_ID,
                    messages=messages,
                    max_completion_tokens=1024,
                    temperature=0.3,
                ),
                timeout=REQUEST_TIMEOUT,
            )

            # Extract content — None if model returned nothing (safety block etc.)
            choice = response.choices[0] if response.choices else None
            if choice is None or choice.message is None or choice.message.content is None:
                last_error_type = "response_content_error"
                log.warning(
                    "phase2_runner: empty response content (attempt %d) — "
                    "possible safety filter block",
                    attempt + 1,
                )
                break  # non-recoverable

            raw_text = choice.message.content

            # ── Kerala food filter — replace non-Kerala terms before sending ────
            raw_text = _apply_kerala_food_filter(raw_text)

            last_raw = raw_text

            # ── Step 14 — Post-generation constraint check ─────────────────────
            ok, violations = check_constraints(raw_text)
            if not ok:
                log.warning(
                    "phase2_runner: CONSTRAINT VIOLATION — violations=%s user=%s",
                    violations, user_id,
                )
                _log_failure(
                    current_message, user_id,
                    "constraint_violation", raw_text,
                    attempt + 1,
                    extra={"violations": violations},
                )
                fb = _make_fallback("constraint_violation", PHASE2_CONSTRAINT_FALLBACK_TEXT)
                fb["constraint_violation"]  = True
                fb["constraint_violations"] = violations
                return fb

            # ── Success ────────────────────────────────────────────────────────
            _t["llm_ms"]   = round((time.perf_counter() - _ts_llm) * 1000)
            _t["total_ms"] = round((time.perf_counter() - _t0) * 1000)
            log.info(
                "phase2_runner: timings %s",
                " | ".join(f"{k}={v}" for k, v in _t.items()),
            )
            log.debug(
                "phase2_runner: success — %d chars, sources=%s, flags=%s (attempt %d)",
                len(raw_text), [c.get("source") for c in top_5_chunks],
                list(active_flags), attempt + 1,
            )

            return {
                "text":                   raw_text.strip(),
                "chunks_used":            [c["chunk_id"] for c in top_5_chunks],
                "chunks_detail":          [
                    {
                        "source":  c["source"],
                        "section": c.get("section_title", ""),
                        "grade":   c.get("grade_priority", 5),
                    }
                    for c in top_5_chunks
                ],
                "condition_flags_active": list(active_flags),
                "query_cache_hit":        query_cache_hit,
                "reranker_scores":        top_5_scores,
                "constraint_violation":   False,
                "constraint_violations":  [],
                "_fallback":              False,
                "_fallback_reason":       "",
                "timings":                _t,
            }

        except asyncio.TimeoutError:
            last_error_type = "timeout"
            log.warning(
                "phase2_runner: timeout after %.1fs (attempt %d/%d)",
                REQUEST_TIMEOUT, attempt + 1, max_retries + 1,
            )
            if attempt < max_retries:
                await asyncio.sleep(RETRY_SLEEP)
                continue

        except openai.RateLimitError:
            last_error_type = "rate_limit_429"
            log.warning(
                "phase2_runner: rate limited — attempt %d/%d",
                attempt + 1, max_retries + 1,
            )
            if attempt < max_retries:
                await asyncio.sleep(RETRY_SLEEP)
                continue

        except openai.APIStatusError as exc:
            if exc.status_code == 503:
                last_error_type = "service_unavailable_503"
                log.error("phase2_runner: model unavailable (503) — falling back immediately")
            else:
                last_error_type = f"api_error:{type(exc).__name__}:{exc.status_code}"
                log.error("phase2_runner: API status error %d: %s", exc.status_code, exc)

        except openai.APIError as exc:
            last_error_type = f"api_error:{type(exc).__name__}"
            log.error("phase2_runner: unexpected API error: %s", exc)

        except Exception as exc:
            # Catch-all — run_phase2() must NEVER raise
            last_error_type = f"api_error:{type(exc).__name__}"
            log.error("phase2_runner: unexpected non-API error: %s", exc)

    # ── Fallback ───────────────────────────────────────────────────────────────
    _log_failure(current_message, user_id, last_error_type, last_raw, max_retries + 1)
    log.error(
        "phase2_runner: returning fallback after %d attempt(s) — reason: %s",
        max_retries + 1, last_error_type,
    )
    return _make_fallback(last_error_type)


# ─────────────────────────────────────────────────────────────────────────────
# Compare-mode context builder (used by compare_runner.py)
#
# Runs Steps 1-11 of the Phase 2 pipeline (retrieval + prompt assembly) without
# calling any LLM. compare_runner fans the returned messages out to all models.
# ─────────────────────────────────────────────────────────────────────────────

async def prepare_rag_context(
    current_message: str,
    session_turns: list,
    phase1_output: dict,
    profile: Optional[dict],
    short_memory: str,
    db_conn,
    embedder,
    reranker,
    user_id: Optional[str] = None,
) -> dict:
    """
    Run Phase 2 retrieval pipeline without calling the LLM.

    On success:
        {"_fallback": False, "messages": [...], "chunks_used": [...], ...}
    On failure:
        {"_fallback": True, "_fallback_reason": "...", "messages": []}
    """
    if phase1_output.get("intent") == "escalation_only":
        fb = _make_fallback("escalation_bypass")
        fb["_fallback"] = False
        fb["messages"] = []
        return fb

    # Step 1 — Build enriched retrieval query
    enriched_query = build_phase2_query(
        current_message=current_message,
        session_turns=session_turns,
        profile=profile,
        mid_clarification_resolved=phase1_output.get("mid_clarification_resolved", False),
    )

    # Step 2 — Resolve condition flags
    stored_flags = (profile or {}).get("condition_flags", [])
    phase1_flags  = phase1_output.get("profile_signals", {}).get("condition_flags", [])
    active_flags  = resolve_condition_flags(
        message=current_message,
        stored_flags=list(set((stored_flags or []) + (phase1_flags or []))),
    )

    # Step 3 — Build retrieval filter
    tier_filter, trigger_filter = build_retrieval_filter(active_flags)

    # Steps 4-7 — Retrieve top chunks (cache-first)
    query_hash      = _compute_query_hash(enriched_query)
    query_cache_hit = False
    top_chunks: list = []

    cached_chunk_ids = await _check_query_cache(db_conn, query_hash)
    if cached_chunk_ids:
        top_chunks      = await _fetch_chunks_by_ids(db_conn, cached_chunk_ids)
        query_cache_hit = True
    else:
        try:
            embedding = await _embed_async(embedder, enriched_query)
        except Exception as exc:
            error_type = f"embed_error:{type(exc).__name__}"
            _log_failure(current_message, user_id, error_type, "", 1)
            return {**_make_fallback(error_type), "messages": []}

        try:
            top_chunks = await _pgvector_search(
                db_conn, embedding, tier_filter, trigger_filter, top_k=TOP_K_ANN
            )
        except Exception as exc:
            error_type = f"pgvector_error:{type(exc).__name__}"
            _log_failure(current_message, user_id, error_type, "", 1)
            return {**_make_fallback(error_type), "messages": []}

        if top_chunks:
            await _write_query_cache(
                db_conn, query_hash, [c["chunk_id"] for c in top_chunks], active_flags
            )

    # Step 8 — Rerank
    if top_chunks:
        try:
            scores = await _rerank_async(reranker, enriched_query, top_chunks)
        except Exception:
            scores = [1.0] * len(top_chunks)

        scored = sorted(zip(scores, top_chunks), key=lambda x: x[0], reverse=True)
        top5_pairs   = scored[:TOP_K_FINAL]
        top5_scores  = [round(s, 4) for s, _ in top5_pairs]
        top5_chunks  = [c for _, c in top5_pairs]
        top5_chunks.sort(key=lambda c: c.get("grade_priority", 5))
    else:
        top5_chunks = []
        top5_scores = []

    # Step 10 — Format chunks for prompt
    chunk_context_block = format_chunks_for_prompt(top5_chunks)

    # Step 11 — Build messages list
    system_prompt = _load_system_prompt()
    messages = _build_openai_messages(
        current_message=current_message,
        session_turns=session_turns,
        short_memory=short_memory,
        chunk_context_block=chunk_context_block,
        system_prompt=system_prompt,
        intent=phase1_output.get("intent"),
        qds_score=phase1_output.get("qds_score"),
        session_context=phase1_output.get("profile_signals", {}).get("session_context"),
        location_hint=(profile or {}).get("location_hint") or "Kerala",
    )

    return {
        "_fallback":              False,
        "_fallback_reason":       "",
        "messages":               messages,
        "chunks_used":            [c["chunk_id"] for c in top5_chunks],
        "chunks_detail":          [
            {"source": c["source"], "section": c.get("section_title", ""), "grade": c.get("grade_priority", 5)}
            for c in top5_chunks
        ],
        "condition_flags_active": list(active_flags),
        "query_cache_hit":        query_cache_hit,
        "reranker_scores":        top5_scores,
    }
