"""
engine/phase1_runner.py — Phase 1 Context Engine runner

Calls gemini-2.0-flash-001 via OpenRouter (OpenAI-compatible API) with the Phase 1
system prompt and structured JSON output. Validates the response and returns a
guaranteed-safe Phase 1 output dict — never raises, never returns a partial result.

Handles all 7 failure modes defined in PHASE1_CONTEXT_ENGINE_SPEC.md Item 4:
  1. Network timeout (>3s)        → asyncio.TimeoutError → PHASE1_FALLBACK
  2. Rate limit (HTTP 429)        → retry once after 1s  → PHASE1_FALLBACK
  3. Model unavailable (HTTP 503) → PHASE1_FALLBACK + error logged
  4. Non-JSON / unparseable text  → json.JSONDecodeError → PHASE1_FALLBACK
  5. Missing qds_score field      → validate_phase1_output() fills default (2)
  6. Missing context_sufficient   → validate_phase1_output() fills default (True)
  7. qds_score outside 1–5        → validate_phase1_output() fills default (2)

LLM routing:
  All calls go through OpenRouter (https://openrouter.ai/api/v1) using the
  OpenAI-compatible chat completions endpoint. The model is specified as
  "google/gemini-2.0-flash-001" — OpenRouter proxies this to Google.

  Note: Gemini context caching is a Google AI Studio / Vertex feature not
  available through OpenRouter. The system prompt is sent on every call.
  At ~2,700 tokens × flash pricing this is negligible.

Usage:
    from engine.phase1_runner import run_phase1

    result = await run_phase1(
        current_message="My feet go numb at night",
        session_turns=[{"role": "bot", "content": "Is it burning or sharp pain?"}],
        user_id="uuid-or-whatsapp-number",
    )
    # result["qds_score"]           → 4
    # result["context_sufficient"]  → False
    # result["_fallback"]           → False  (real response)

Environment:
    OPENROUTER_API_KEY — required. Set in .env at project root.
"""

import asyncio
import copy
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import openai

from schemas.phase1_schema import (
    PHASE1_FALLBACK,
    validate_phase1_output,
)

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

MODEL_ID         = "google/gemini-2.5-flash-lite"
OPENROUTER_URL   = "https://openrouter.ai/api/v1"
REQUEST_TIMEOUT  = 20.0
RETRY_SLEEP      = 5.0

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "phase1_system_prompt.txt"
LOG_PATH    = Path(__file__).parent.parent / "logs" / "phase1_failures.jsonl"

# ─────────────────────────────────────────────────────────────────────────────
# Module-level state
# ─────────────────────────────────────────────────────────────────────────────

_system_prompt_text: Optional[str] = None


def _reset_module_state() -> None:
    """
    Reset module-level state. Called by tests between test cases to prevent
    state bleed-through. Not called in production.
    """
    global _system_prompt_text
    _system_prompt_text = None


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_system_prompt() -> str:
    """Load the system prompt from disk. Cached in module state after first read."""
    global _system_prompt_text
    if _system_prompt_text is None:
        _system_prompt_text = PROMPT_PATH.read_text(encoding="utf-8")
        token_estimate = len(_system_prompt_text) // 4
        log.debug(
            "phase1_runner: system prompt loaded (~%d tokens)",
            token_estimate,
        )
    return _system_prompt_text


def _build_messages(session_turns: list, current_message: str, system_prompt: str) -> list:
    """
    Build the OpenAI-format messages list from system prompt + session history
    + current patient message.

    Maps roles: patient → "user", bot → "assistant".
    Includes at most the last 5 turns (sufficient for mid-clarification detection
    with Option C — no DB flag needed).
    Always appends current_message as the final "user" turn.

    Args:
        session_turns:   Prior turns as [{"role": "patient"/"bot", "content": "..."}].
                         Does NOT include the current message.
        current_message: The current patient message to classify.
        system_prompt:   Phase 1 system prompt text.

    Returns:
        List of {"role": ..., "content": ...} dicts for the chat completions API.
    """
    messages = [{"role": "system", "content": system_prompt}]

    for turn in session_turns[-5:]:
        role = "assistant" if turn.get("role") == "bot" else "user"
        messages.append({"role": role, "content": turn.get("content", "")})

    messages.append({"role": "user", "content": current_message})
    return messages


def _make_fallback(error_type: str) -> dict:
    """
    Return a deep copy of PHASE1_FALLBACK stamped with the error type.
    Deep copy is required — PHASE1_FALLBACK has a nested profile_signals dict
    that would be shared across calls if shallow-copied.
    """
    fb = copy.deepcopy(PHASE1_FALLBACK)
    fb["_fallback"] = True
    fb["_fallback_reason"] = error_type
    return fb


def _log_failure(
    current_message: str,
    user_id: Optional[str],
    error_type: str,
    raw_output: str,
    attempt_count: int,
) -> None:
    """
    Append one failure record to logs/phase1_failures.jsonl.

    Schema (matches PHASE1_CONTEXT_ENGINE_SPEC.md Item 4c):
        timestamp       ISO-8601 UTC
        user_id         patient identifier (or "unknown")
        message_id      UUID for this specific failure event
        error_type      one of the 7 failure type strings
        raw_output      first 2000 chars of whatever the model returned (or "")
        fallback_applied always True (this is only called when fallback fires)
        attempt_count   how many attempts were made before giving up

    Ops review: Phase 1 failure rate should be reviewed weekly.
    If >2% of turns hit fallback, investigate model stability.
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "user_id":         user_id or "unknown",
        "message_id":      str(uuid.uuid4()),
        "error_type":      error_type,
        "raw_output":      raw_output[:2000],
        "fallback_applied": True,
        "attempt_count":   attempt_count,
    }
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as log_exc:
        log.error("phase1_runner: could not write to failure log: %s", log_exc)


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

async def run_phase1(
    current_message: str,
    session_turns: list,
    user_id: Optional[str] = None,
    max_retries: int = 1,
) -> dict:
    """
    Run Phase 1 classification for one patient message.

    Always returns a valid Phase 1 output dict — never raises.

    On success:    result["_fallback"] == False
    On any error:  result["_fallback"] == True
                   result["_fallback_reason"] == "<error_type>"

    Failure type strings:
        "missing_api_key"           OPENROUTER_API_KEY env var not set
        "timeout"                   model did not respond within REQUEST_TIMEOUT
        "rate_limit_429"            HTTP 429 — retried once, still failed
        "service_unavailable_503"   HTTP 503 — model temporarily unavailable
        "json_decode_error:*"       model returned non-JSON or unparseable text
        "api_error:*"               any other API exception
        "response_content_error"    response had no content

    Args:
        current_message:  Patient's message in English (post-translation).
        session_turns:    Last N prior turns as [{"role": "patient"/"bot",
                          "content": "..."}]. Does NOT include current_message.
        user_id:          Patient identifier for failure log. Optional.
        max_retries:      Retry attempts for recoverable errors (timeout, 429).
                          Default 1 — one retry before fallback fires.

    Returns:
        Validated Phase 1 output dict with all 6 required fields plus _fallback
        and _fallback_reason internal flags.
    """
    # ── Guard: API key ─────────────────────────────────────────────────────────
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        log.error("phase1_runner: OPENROUTER_API_KEY not set — returning fallback immediately")
        _log_failure(current_message, user_id, "missing_api_key", "", 0)
        return _make_fallback("missing_api_key")

    system_prompt = _load_system_prompt()
    messages      = _build_messages(session_turns, current_message, system_prompt)

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

    # ── Retry loop ─────────────────────────────────────────────────────────────
    for attempt in range(max_retries + 1):
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=MODEL_ID,
                    messages=messages,
                    response_format={"type": "json_object"},
                    max_completion_tokens=2048,
                    temperature=0.1,
                ),
                timeout=REQUEST_TIMEOUT,
            )

            # Extract content — None if model returned nothing
            choice = response.choices[0] if response.choices else None
            if choice is None or choice.message is None or choice.message.content is None:
                last_error_type = "response_content_error"
                log.warning(
                    "phase1_runner: empty response content (attempt %d) — "
                    "possible safety filter block",
                    attempt + 1,
                )
                break  # non-recoverable

            raw_text = choice.message.content
            last_raw = raw_text

            # Parse JSON
            try:
                parsed = json.loads(raw_text)
            except (json.JSONDecodeError, ValueError) as parse_err:
                last_error_type = f"json_decode_error:{type(parse_err).__name__}"
                log.warning(
                    "phase1_runner: JSON parse failed on attempt %d: %s",
                    attempt + 1, parse_err,
                )
                break  # structured output failures are non-recoverable

            # Validate and fill defaults for any missing/invalid fields
            validated = validate_phase1_output(parsed)
            validated["_fallback"]        = False
            validated["_fallback_reason"] = ""

            log.debug(
                "phase1_runner: success — intent=%s qds=%d sufficient=%s (attempt %d)",
                validated.get("intent"), validated.get("qds_score"),
                validated.get("context_sufficient"), attempt + 1,
            )
            return validated

        except asyncio.TimeoutError:
            last_error_type = "timeout"
            log.warning(
                "phase1_runner: timeout after %.1fs (attempt %d/%d)",
                REQUEST_TIMEOUT, attempt + 1, max_retries + 1,
            )
            if attempt < max_retries:
                await asyncio.sleep(RETRY_SLEEP)
                continue  # retry

        except openai.RateLimitError:
            last_error_type = "rate_limit_429"
            log.warning(
                "phase1_runner: rate limited — attempt %d/%d",
                attempt + 1, max_retries + 1,
            )
            if attempt < max_retries:
                await asyncio.sleep(RETRY_SLEEP)
                continue  # retry

        except openai.APIStatusError as exc:
            if exc.status_code == 503:
                last_error_type = "service_unavailable_503"
                log.error("phase1_runner: model unavailable (503) — falling back immediately")
                break  # No retry for 503 — sleeping won't help
            else:
                last_error_type = f"api_error:{type(exc).__name__}:{exc.status_code}"
                log.error("phase1_runner: API status error %d: %s", exc.status_code, exc)
                break

        except openai.APIError as exc:
            last_error_type = f"api_error:{type(exc).__name__}"
            log.error("phase1_runner: unexpected API error: %s", exc)
            break  # Non-recoverable

        except Exception as exc:
            # Catch-all — run_phase1() must NEVER raise
            last_error_type = f"api_error:{type(exc).__name__}"
            log.error("phase1_runner: unexpected non-API error: %s", exc)
            break

    # ── Fallback ───────────────────────────────────────────────────────────────
    fallback = _make_fallback(last_error_type)
    _log_failure(current_message, user_id, last_error_type, last_raw, max_retries + 1)
    log.error(
        "phase1_runner: returning fallback after %d attempt(s) — reason: %s",
        max_retries + 1, last_error_type,
    )
    return fallback
