"""
engine/compare_runner.py -- Parallel multi-model runner for compare mode

Flow (matches chat mode):
  1. Run Phase 1 (context analysis, intent, QDS score)
  2. Run RAG pipeline via prepare_rag_context() — embed → ANN → rerank → format chunks
  3. Fan out the same RAG-enriched messages to all models in parallel
  4. Yield SSE results as each model completes

All models receive the identical Phase 2 prompt (same clinical context, same patient
memory, same session history) — the only variable is which LLM generates the response.
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import AsyncGenerator, Optional

import httpx
from dotenv import load_dotenv

from engine.phase1_runner import run_phase1
from engine.phase2_runner import prepare_rag_context

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

PROVIDERS: dict = {
    "groq":     {"base_url": "https://api.groq.com/openai/v1",  "env_key": "GROQ_API_KEY"},
    "cerebras": {"base_url": "https://api.cerebras.ai/v1",      "env_key": "CEREBRAS_API_KEY"},
}

# OpenRouter uses a fixed curated list instead of live /models discovery.
# Only fast and mid-tier models — no high-reasoning/high-cost variants.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS = [
    "google/gemini-2.5-flash",
    "openai/gpt-4o-mini",
    "openai/gpt-4.1-mini",
    "anthropic/claude-3-haiku",
    "anthropic/claude-3.5-haiku",
    "anthropic/claude-sonnet-4.6",
    "x-ai/grok-4.3",
    "deepseek/deepseek-chat-v3-0324",
    "deepseek/deepseek-r1-0528",
]

_SKIP_PATTERNS = ("whisper", "orpheus", "prompt-guard", "safeguard")
_MIN_CONTEXT   = 1024


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sse(event_type: str, **kwargs) -> str:
    return f"data: {json.dumps({'type': event_type, **kwargs})}\n\n"


def _is_chat_model(m: dict) -> bool:
    mid = m["id"].lower()
    ctx = m.get("context_window")
    if ctx is not None and ctx < _MIN_CONTEXT:
        return False
    return not any(pat in mid for pat in _SKIP_PATTERNS)


def _supports_reasoning(model_id: str) -> bool:
    mid = model_id.lower()
    return "gpt-oss" in mid or "deepseek-r1" in mid


def _clean_output(text: str) -> str:
    """
    Clean model output for display. Handles:
    1. BPE byte-level encoding artifacts (deepseek-r1-distill and similar)
    2. <think> blocks (qwen3, deepseek reasoning models)
    3. Leaked prompt XML tags (<phase1_context>, <patient_memory>, <clinical_context>)
    4. Internal meta-notes like (Note: Grounded in clinical evidence...)
    5. Markdown noise (headers, bullets, bold)
    6. Preamble filler ("Here are some...", "Based on your question...")
    7. Trailing meta ("Let me know if you have any other questions!")
    8. Numbered list markers (1. 2. 3.)
    9. Emoji
    """
    # ── BPE byte-level encoding artifacts ─────────────────────────────────────
    # GPT-2 byte-level BPE: U+0120 (Ġ) = space, U+010A (Ċ) = newline
    if 'Ġ' in text or 'Ċ' in text:
        text = text.replace('Ġ', ' ').replace('Ċ', '\n').replace('č', '\r')

    # ── <think> blocks ────────────────────────────────────────────────────────
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<think>.*', '', text, flags=re.DOTALL)

    # ── Leaked prompt XML blocks ───────────────────────────────────────────────
    # gemini-2.5-flash-lite echoes back the injected context; strip it entirely
    text = re.sub(
        r'<(?:phase1_context|patient_memory|clinical_context)>.*?'
        r'</(?:phase1_context|patient_memory|clinical_context)>',
        '', text, flags=re.DOTALL | re.IGNORECASE,
    )

    # ── Internal meta-notes ───────────────────────────────────────────────────
    # e.g. "(Note: Grounded in clinical evidence...from the provided chunks.)"
    text = re.sub(r'\(Note:[^)]*\)', '', text, flags=re.IGNORECASE)

    # ── Markdown noise ────────────────────────────────────────────────────────
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
    text = re.sub(r'^[-*]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)

    # ── Preamble filler ───────────────────────────────────────────────────────
    text = re.sub(
        r'^(?:Here (?:is|are)(?: a| some)?\s[^:\n]{0,60}:\s*|'
        r'Based on your question[^,\n]{0,60},\s*here\'s what[^:\n]{0,60}:\s*|'
        r'Of course[!,][^\n]{0,80}\.\s*)',
        '', text, flags=re.IGNORECASE,
    )

    # ── Trailing meta ─────────────────────────────────────────────────────────
    text = re.sub(
        r'\s*(?:Let me know if you have any (?:other )?questions?[.!]?|'
        r'Feel free to (?:ask|reach out)[^.!]{0,60}[.!]?|'
        r'I hope this helps?[.!]?)$',
        '', text, flags=re.IGNORECASE,
    )

    # ── Emoji ─────────────────────────────────────────────────────────────────
    text = re.sub(
        r'[\U0001F300-\U0001F9FF\U00002700-\U000027BF\U0001FA00-\U0001FA6F]',
        '', text,
    )

    # ── Whitespace normalisation ──────────────────────────────────────────────
    text = re.sub(r'\n{2,}', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------

async def _fetch_models(provider: str, client: httpx.AsyncClient) -> list:
    cfg     = PROVIDERS[provider]
    api_key = os.environ.get(cfg["env_key"], "").strip()
    if not api_key:
        return []
    try:
        resp = await client.get(
            f"{cfg['base_url']}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        return [
            {**m, "_provider": provider, "_api_key": api_key, "_base_url": cfg["base_url"]}
            for m in resp.json()["data"]
            if _is_chat_model(m)
        ]
    except Exception:
        return []


async def _fetch_openrouter_models(client: httpx.AsyncClient) -> list:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return []
    return [
        {
            "id":        model_id,
            "_provider": "openrouter",
            "_api_key":  api_key,
            "_base_url": OPENROUTER_BASE_URL,
        }
        for model_id in OPENROUTER_MODELS
    ]


# ---------------------------------------------------------------------------
# Single model call — accepts pre-built RAG messages
# ---------------------------------------------------------------------------

async def _call_model(
    model_info: dict,
    messages: list,
    client: httpx.AsyncClient,
) -> dict | None:
    """
    Call one model with the pre-built RAG-enriched messages list.
    Returns None on failure (silently skipped by caller).
    """
    provider = model_info["_provider"]
    model_id = model_info["id"]
    api_key  = model_info["_api_key"]
    base_url = model_info["_base_url"]

    body: dict = {
        "model":      model_id,
        "messages":   messages,
        "temperature": 0.3,
        "max_tokens":  1024,
    }
    if _supports_reasoning(model_id):
        body["reasoning_effort"] = "medium"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://preventify.in"
        headers["X-Title"]      = "Preventify Diabetes Educator"

    t0 = time.time()
    try:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=body,
            timeout=90,
        )
        elapsed = round(time.time() - t0, 2)
        resp.raise_for_status()
        data   = resp.json()
        choice = data["choices"][0]
        usage  = data.get("usage", {})
        msg    = choice["message"]
        output = _clean_output(msg.get("content") or "")
        if not output:
            return None
        return {
            "provider":      provider,
            "model":         model_id,
            "text":          output,
            "latency_s":     elapsed,
            "input_tokens":  usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main stream generator
# ---------------------------------------------------------------------------

async def run_compare_stream(
    message: str,
    session_turns: list,
    profile: Optional[dict],
    short_memory: str,
    db_conn,
    embedder,
    reranker,
    user_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    Async generator yielding SSE strings.

    Pipeline:
      1. Phase 1 — classify intent, detect QDS, extract condition flags
      2. RAG — embed query → ANN search → rerank → build enriched messages
      3. Fan out identical RAG messages to all models in parallel
      4. Yield model_result as each model finishes
    """
    # ── Phase 1 ───────────────────────────────────────────────────────────────
    yield _sse("status", text="Analysing question...")
    phase1_output = await run_phase1(
        current_message=message,
        session_turns=session_turns,
        user_id=user_id,
    )

    # ── RAG context ───────────────────────────────────────────────────────────
    yield _sse("status", text="Retrieving clinical context...")
    rag = await prepare_rag_context(
        current_message=message,
        session_turns=session_turns,
        phase1_output=phase1_output,
        profile=profile,
        short_memory=short_memory,
        db_conn=db_conn,
        embedder=embedder,
        reranker=reranker,
        user_id=user_id,
    )

    if rag.get("_fallback") and not rag.get("messages"):
        yield _sse("error", text="Could not retrieve clinical context. Check API keys.")
        yield _sse("compare_done", total=0)
        return

    messages = rag["messages"]

    async with httpx.AsyncClient() as client:
        # ── Discover models ────────────────────────────────────────────────────
        yield _sse("status", text="Connecting to models...")
        model_lists = await asyncio.gather(
            *[_fetch_models(p, client) for p in PROVIDERS],
            _fetch_openrouter_models(client),
            return_exceptions=True,
        )
        all_models: list = []
        for ml in model_lists:
            if isinstance(ml, list):
                all_models.extend(ml)

        if not all_models:
            yield _sse("error", text="No models available. Check API keys in .env.")
            yield _sse("compare_done", total=0)
            return

        yield _sse("compare_start", total=len(all_models))

        # ── Fan out — all models get the same RAG-enriched messages ───────────
        pending: set = {
            asyncio.ensure_future(_call_model(m, messages, client))
            for m in all_models
        }

        success = 0
        collected: list = []
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    result = task.result()
                except Exception:
                    result = None
                if result is not None:
                    yield _sse("model_result", **result)
                    collected.append(result)
                    success += 1

        # ── Persist run to logs/ ───────────────────────────────────────────────
        try:
            from datetime import datetime
            logs_dir = BASE_DIR / "logs"
            logs_dir.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = logs_dir / f"model_compare_{ts}.json"
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump({"timestamp": ts, "prompt": message, "results": collected}, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

        yield _sse("compare_done", total=success)
