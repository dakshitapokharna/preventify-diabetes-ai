"""
api/memory_compressor.py — Session memory compressor

Called at session end (10 patient turns). Compresses all turns from the
completed session into a ~100-token clinical summary stored in users.short_memory.

Uses Gemini 2.0 Flash via OpenRouter — same model as Phase 1, cheap call.
Never raises — failures are logged; short_memory stays unchanged on failure.
"""

import json
import logging
import os
from typing import List

import httpx

log = logging.getLogger(__name__)

CEREBRAS_URL   = "https://api.cerebras.ai/v1/chat/completions"
MODEL_ID       = "qwen-3-235b-a22b-instruct-2507"
REQUEST_TIMEOUT = 15.0  # seconds — generous for end-of-session, not blocking patient

COMPRESS_SYSTEM_PROMPT = (
    "You are a clinical note compressor for a diabetes education chatbot. "
    "Summarise the conversation in 80–100 tokens. "
    "Include: detected diabetes type, medications mentioned, main concerns raised, "
    "complications mentioned. "
    "Format: plain sentences. No diagnosis. No doses. No medication changes."
)


def _build_conversation_text(turns: list) -> str:
    """Format session turns into a plain text transcript for the compressor."""
    lines = []
    for t in turns:
        role = "Patient" if t.get("role") == "patient" else "Bot"
        content = (t.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def compress_session(turns: List[dict]) -> str:
    """
    Compress a list of session turns into a ~100-token clinical summary.

    Args:
        turns: List of {"role": "patient"|"bot", "content": str} dicts.

    Returns:
        Compressed summary string, or "" on failure.
    """
    if not turns:
        return ""

    conversation_text = _build_conversation_text(turns)
    if not conversation_text.strip():
        return ""

    api_key = os.environ.get("CEREBRAS_API_KEY", "")
    if not api_key:
        log.error("memory_compressor: CEREBRAS_API_KEY not set — skipping compression")
        return ""

    payload = {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": COMPRESS_SYSTEM_PROMPT},
            {"role": "user",   "content": conversation_text},
        ],
        "temperature": 0.2,
        "max_tokens": 150,  # hard cap — prevents runaway
    }

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(
                CEREBRAS_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(payload),
            )
        resp.raise_for_status()
        data = resp.json()
        summary = data["choices"][0]["message"]["content"].strip()
        log.debug("memory_compressor: compressed %d turns → %d chars", len(turns), len(summary))
        return summary

    except httpx.TimeoutException:
        log.error("memory_compressor: timeout after %.1fs", REQUEST_TIMEOUT)
    except httpx.HTTPStatusError as e:
        log.error("memory_compressor: HTTP %d — %s", e.response.status_code, e.response.text[:200])
    except Exception as exc:
        log.error("memory_compressor: unexpected error — %s: %s", type(exc).__name__, exc)

    return ""


async def update_short_memory(user_id: str, summary: str, db_conn) -> None:
    """
    Write the compressed summary to users.short_memory.
    Also increments total_sessions and updates last_session_date.
    Never raises.
    """
    if not summary:
        return
    try:
        await db_conn.execute(
            """
            UPDATE users
            SET short_memory      = $2,
                total_sessions    = total_sessions + 1,
                last_session_date = CURRENT_DATE
            WHERE user_id = $1
            """,
            user_id,
            summary,
        )
        log.debug("memory_compressor: short_memory updated for user=%s", user_id)
    except Exception as exc:
        log.error(
            "memory_compressor: failed to update short_memory for user=%s — %s: %s",
            user_id, type(exc).__name__, exc,
        )
