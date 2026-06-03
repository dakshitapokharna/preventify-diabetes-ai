"""
engine/compare_runner.py -- Parallel multi-model runner for compare mode

Fetches available chat models from Groq + Cerebras live from their APIs,
runs all of them in parallel, and yields SSE events as each model finishes.
Failed models are silently skipped -- no error event reaches the frontend.
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import AsyncGenerator

import httpx
from dotenv import load_dotenv

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
    "google/gemini-2.5-flash-lite",
    "google/gemini-2.5-flash",
    "openai/gpt-4o-mini",
    "openai/gpt-4.1-mini",
    "anthropic/claude-3-haiku",
    "anthropic/claude-3.5-haiku",
    "anthropic/claude-sonnet-4.6",
    "x-ai/grok-4.3",
    "deepseek/deepseek-chat-v3-0324",
    "deepseek/deepseek-r1-distill-qwen-32b",
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


def _load_system_prompt() -> str:
    # Compare mode uses a dedicated short prompt — strict 2-sentence rule, no markdown.
    # The full Phase 2 prompt is too complex (RAG chunk rules, patient memory blocks, etc.)
    # and causes models to produce verbose or meta-commentary responses.
    path = BASE_DIR / "prompts" / "compare_system_prompt.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (
        "You are Preventify, a certified diabetes educator for patients in Kerala, India. "
        "Respond in EXACTLY 2 sentences. No markdown. No bullet points. No internal reasoning."
    )


def _supports_reasoning(model_id: str) -> bool:
    mid = model_id.lower()
    return "gpt-oss" in mid or "deepseek-r1" in mid


def _clean_output(text: str) -> str:
    """
    Clean model output for display:
    1. Strip <think>...</think> blocks (qwen3, deepseek-style models).
       Also handles unclosed <think> tags — when 512-token cutoff chops off </think>,
       the regex wouldn't match; the second sub catches the orphaned opening tag.
    2. Strip markdown formatting (headers, bullets, bold).
    3. Collapse extra whitespace.
    """
    # Remove complete <think>...</think> blocks
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # Remove any orphaned opening <think> tag and everything after it
    text = re.sub(r'<think>.*', '', text, flags=re.DOTALL)
    # Strip markdown: headers, bullets, bold/italic markers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
    text = re.sub(r'^[-*]\s+', '', text, flags=re.MULTILINE)
    # Collapse whitespace
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


# ---------------------------------------------------------------------------
# OpenRouter model listing (static curated list, no live /models call)
# ---------------------------------------------------------------------------

async def _fetch_openrouter_models(client: httpx.AsyncClient) -> list:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return []
    return [
        {
            "id":         model_id,
            "_provider":  "openrouter",
            "_api_key":   api_key,
            "_base_url":  OPENROUTER_BASE_URL,
        }
        for model_id in OPENROUTER_MODELS
    ]


# ---------------------------------------------------------------------------
# Single model call
# ---------------------------------------------------------------------------

async def _call_model(
    model_info: dict,
    message: str,
    system_prompt: str,
    client: httpx.AsyncClient,
) -> dict | None:
    provider = model_info["_provider"]
    model_id = model_info["id"]
    api_key  = model_info["_api_key"]
    base_url = model_info["_base_url"]

    body: dict = {
        "model":    model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": message},
        ],
        "temperature": 1,
        "max_tokens":  512,
    }
    if _supports_reasoning(model_id):
        body["reasoning_effort"] = "medium"

    headers = {
        "Authorization":  f"Bearer {api_key}",
        "Content-Type":   "application/json",
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
        # Only use `content` — `reasoning` is the internal thinking trace, not the answer.
        # Models that never populate `content` (e.g. zai-glm-4.7) are skipped silently.
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

async def run_compare_stream(message: str) -> AsyncGenerator[str, None]:
    """
    Async generator yielding SSE strings.
    All models run in parallel; results yielded as each completes.
    """
    system_prompt = _load_system_prompt()

    async with httpx.AsyncClient() as client:
        # Fetch model lists from all providers in parallel
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

        # Launch all model calls as asyncio Tasks in parallel
        pending: set = {
            asyncio.ensure_future(_call_model(m, message, system_prompt, client))
            for m in all_models
        }

        success = 0
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    result = task.result()
                except Exception:
                    result = None
                if result is not None:
                    yield _sse("model_result", **result)
                    success += 1

        yield _sse("compare_done", total=success)
