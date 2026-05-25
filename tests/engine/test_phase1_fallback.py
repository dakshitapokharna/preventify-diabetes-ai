"""
tests/engine/test_phase1_fallback.py — Unit tests for engine/phase1_runner.py

Tests all 7 failure modes defined in PHASE1_CONTEXT_ENGINE_SPEC.md Item 4,
using Cerebras API (openai-compatible SDK):

  1. missing_api_key         — CEREBRAS_API_KEY not set
  2. timeout                 — asyncio.TimeoutError after REQUEST_TIMEOUT seconds
  3. rate_limit_429          — openai.RateLimitError, retried once then fallback
  4. service_unavailable_503 — openai.APIStatusError status_code=503, no retry
  5. json_decode_error       — model returns non-JSON text
  6. api_error               — openai.APIError (unexpected exception)
  7. response_content_error  — response.choices is empty or content is None

Also tests:
  - Happy path: valid JSON → validate_phase1_output() called → _fallback=False
  - QDS 1/2/5 always context_sufficient=True (validate_phase1_output override)
  - Deep copy: PHASE1_FALLBACK not mutated across calls
  - _build_messages(): role mapping, max 5 turns, system prompt position
  - Failure log: JSONL record written with correct schema on every fallback

Run:
    pytest tests/engine/test_phase1_fallback.py -v
"""

import asyncio
import copy
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

import engine.phase1_runner as runner
from engine.phase1_runner import _build_messages, _reset_module_state, run_phase1
from schemas.phase1_schema import PHASE1_FALLBACK

# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures and helpers
# ─────────────────────────────────────────────────────────────────────────────

VALID_PHASE1_JSON = json.dumps(
    {
        "intent": "healthy_eating",
        "qds_score": 2,
        "context_sufficient": True,
        "clarifying_questions": [],
        "profile_signals": {
            "diabetes_type": "T2DM",
            "medications_mentioned": ["metformin"],
            "insulin_user": False,
            "condition_flags": [],
            "complications_mentioned": [],
            "location_hint": "",
            "session_context": "self",
        },
        "mid_clarification_resolved": False,
    }
)

DUMMY_SYSTEM_PROMPT = "You are a test educator."


@pytest.fixture(autouse=True)
def reset_module_state():
    """Reset module-level state before and after every test."""
    _reset_module_state()
    yield
    _reset_module_state()


def make_mock_client(
    response_text: str = VALID_PHASE1_JSON,
    raise_on_generate=None,
    empty_choices: bool = False,
    null_content: bool = False,
):
    """
    Build a fully-mocked openai.AsyncOpenAI client.

    Args:
        response_text:     Text returned as choices[0].message.content.
        raise_on_generate: Exception to raise from chat.completions.create.
        empty_choices:     Return a response with choices=[] (simulates safety block).
        null_content:      Return a response with choices[0].message.content = None.
    """
    client = MagicMock()

    if raise_on_generate:
        client.chat.completions.create = AsyncMock(side_effect=raise_on_generate)
    elif empty_choices:
        mock_response = MagicMock()
        mock_response.choices = []
        client.chat.completions.create = AsyncMock(return_value=mock_response)
    elif null_content:
        mock_choice = MagicMock()
        mock_choice.message.content = None
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client.chat.completions.create = AsyncMock(return_value=mock_response)
    else:
        mock_choice = MagicMock()
        mock_choice.message.content = response_text
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        client.chat.completions.create = AsyncMock(return_value=mock_response)

    return client


def _make_rate_limit_error():
    """Create a real openai.RateLimitError with minimal mock response."""
    mock_request = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {}
    mock_response.request = mock_request
    mock_response.json.return_value = {}
    return openai.RateLimitError("rate limited", response=mock_response, body={})


def _make_503_error():
    """Create a real openai.APIStatusError with status_code=503."""
    mock_request = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.headers = {}
    mock_response.request = mock_request
    mock_response.json.return_value = {}
    return openai.APIStatusError("service unavailable", response=mock_response, body={})


def _make_api_connection_error():
    """Create a real openai.APIConnectionError (subclass of openai.APIError)."""
    mock_request = MagicMock()
    return openai.APIConnectionError(message="connection failed", request=mock_request)


# ─────────────────────────────────────────────────────────────────────────────
# Failure Mode 1 — missing OPENROUTER_API_KEY
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_api_key_returns_fallback_immediately(tmp_path, monkeypatch):
    """
    Failure Mode 1: CEREBRAS_API_KEY env var not set.
    Fallback fires before any network call. No retry sleep.
    """
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")

    result = await run_phase1("My feet go numb at night", [], user_id="test-user")

    assert result["_fallback"] is True
    assert result["_fallback_reason"] == "missing_api_key"
    # Fallback always allows Phase 2 to proceed — never blocks the patient
    assert result["context_sufficient"] is True
    assert result["qds_score"] == 2
    assert result["intent"] == "general_dsmes"


@pytest.mark.asyncio
async def test_missing_api_key_no_network_call(tmp_path, monkeypatch):
    """CEREBRAS_API_KEY missing → openai.AsyncOpenAI is never constructed."""
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")

    with patch("engine.phase1_runner.openai.AsyncOpenAI") as mock_client_cls:
        await run_phase1("test", [])
        mock_client_cls.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Failure Mode 2 — Network timeout (>3s)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_retries_then_returns_fallback(tmp_path, monkeypatch):
    """
    Failure Mode 2: asyncio.TimeoutError → retry once after RETRY_SLEEP → fallback.
    With max_retries=1: attempt 1 times out, attempt 2 times out, fallback fires.
    """
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    sleep_mock = AsyncMock()
    client = make_mock_client()
    with patch("engine.phase1_runner.asyncio.wait_for", side_effect=asyncio.TimeoutError):
        with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
            with patch("engine.phase1_runner.asyncio.sleep", sleep_mock):
                result = await run_phase1("test", [], max_retries=1)

    assert result["_fallback"] is True
    assert result["_fallback_reason"] == "timeout"
    # Slept once between the two attempts
    sleep_mock.assert_called_once_with(runner.RETRY_SLEEP)


@pytest.mark.asyncio
async def test_timeout_with_zero_retries_no_sleep(tmp_path, monkeypatch):
    """Failure Mode 2: max_retries=0 → single attempt, no sleep on timeout."""
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    sleep_mock = AsyncMock()
    client = make_mock_client()
    with patch("engine.phase1_runner.asyncio.wait_for", side_effect=asyncio.TimeoutError):
        with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
            with patch("engine.phase1_runner.asyncio.sleep", sleep_mock):
                result = await run_phase1("test", [], max_retries=0)

    assert result["_fallback"] is True
    assert result["_fallback_reason"] == "timeout"
    sleep_mock.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Failure Mode 3 — Rate limit (openai.RateLimitError / HTTP 429)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limit_retries_then_returns_fallback(tmp_path, monkeypatch):
    """
    Failure Mode 3: openai.RateLimitError → retry once → fallback.
    With max_retries=1: generate called twice, slept once.
    """
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    exc = _make_rate_limit_error()
    client = make_mock_client(raise_on_generate=exc)

    sleep_mock = AsyncMock()
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        with patch("engine.phase1_runner.asyncio.sleep", sleep_mock):
            result = await run_phase1("test", [], max_retries=1)

    assert result["_fallback"] is True
    assert result["_fallback_reason"] == "rate_limit_429"
    sleep_mock.assert_called_once_with(runner.RETRY_SLEEP)
    assert client.chat.completions.create.call_count == 2  # two attempts


@pytest.mark.asyncio
async def test_rate_limit_zero_retries_no_sleep(tmp_path, monkeypatch):
    """Rate limit with max_retries=0 → single attempt, no sleep."""
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    client = make_mock_client(raise_on_generate=_make_rate_limit_error())
    sleep_mock = AsyncMock()
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        with patch("engine.phase1_runner.asyncio.sleep", sleep_mock):
            result = await run_phase1("test", [], max_retries=0)

    assert result["_fallback"] is True
    assert result["_fallback_reason"] == "rate_limit_429"
    sleep_mock.assert_not_called()
    assert client.chat.completions.create.call_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# Failure Mode 4 — Service unavailable (HTTP 503)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_503_no_retry_immediate_fallback(tmp_path, monkeypatch):
    """
    Failure Mode 4: openai.APIStatusError status_code=503 → no retry (model is down).
    Even with max_retries=1, generate called exactly once.
    """
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    client = make_mock_client(raise_on_generate=_make_503_error())

    sleep_mock = AsyncMock()
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        with patch("engine.phase1_runner.asyncio.sleep", sleep_mock):
            result = await run_phase1("test", [], max_retries=1)

    assert result["_fallback"] is True
    assert result["_fallback_reason"] == "service_unavailable_503"
    # MUST NOT sleep or retry for 503
    sleep_mock.assert_not_called()
    assert client.chat.completions.create.call_count == 1


@pytest.mark.asyncio
async def test_non_503_api_status_error_returns_api_error(tmp_path, monkeypatch):
    """
    openai.APIStatusError with a non-503 status code (e.g. 502) → api_error fallback.
    """
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    mock_request = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 502
    mock_response.headers = {}
    mock_response.request = mock_request
    mock_response.json.return_value = {}
    exc_502 = openai.APIStatusError("bad gateway", response=mock_response, body={})

    client = make_mock_client(raise_on_generate=exc_502)
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        result = await run_phase1("test", [])

    assert result["_fallback"] is True
    assert result["_fallback_reason"].startswith("api_error:")


# ─────────────────────────────────────────────────────────────────────────────
# Failure Mode 5 — Non-JSON / unparseable text
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_json_decode_error_returns_fallback(tmp_path, monkeypatch):
    """Failure Mode 5: model returns plain text → json.JSONDecodeError → fallback."""
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    client = make_mock_client(response_text="I'm sorry, I cannot answer that.")
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        result = await run_phase1("test", [])

    assert result["_fallback"] is True
    assert result["_fallback_reason"].startswith("json_decode_error:")


@pytest.mark.asyncio
async def test_json_decode_does_not_retry(tmp_path, monkeypatch):
    """
    Failure Mode 5: structured output failure is non-recoverable.
    No sleep, generate called exactly once even with max_retries=1.
    """
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    client = make_mock_client(response_text="{{this is not valid json}}")
    sleep_mock = AsyncMock()
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        with patch("engine.phase1_runner.asyncio.sleep", sleep_mock):
            result = await run_phase1("test", [], max_retries=1)

    assert result["_fallback"] is True
    sleep_mock.assert_not_called()
    assert client.chat.completions.create.call_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# Failure Mode 6 — Unexpected API error (openai.APIError)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_connection_error_returns_fallback(tmp_path, monkeypatch):
    """Failure Mode 6: openai.APIConnectionError → api_error:<ClassName> fallback."""
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    client = make_mock_client(raise_on_generate=_make_api_connection_error())
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        result = await run_phase1("test", [])

    assert result["_fallback"] is True
    assert result["_fallback_reason"] == "api_error:APIConnectionError"


@pytest.mark.asyncio
async def test_unexpected_runtime_error_returns_fallback(tmp_path, monkeypatch):
    """
    Catch-all: even a non-openai exception (e.g. RuntimeError from a bug) must not
    propagate — run_phase1() must always return a fallback dict.
    """
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    client = make_mock_client(raise_on_generate=RuntimeError("internal model error"))
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        result = await run_phase1("test", [])

    assert result["_fallback"] is True
    assert result["_fallback_reason"] == "api_error:RuntimeError"


# ─────────────────────────────────────────────────────────────────────────────
# Failure Mode 7 — Empty response / no content
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_choices_returns_content_error(tmp_path, monkeypatch):
    """
    Failure Mode 7: response.choices is empty (safety filter block).
    _fallback_reason == 'response_content_error'.
    """
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    client = make_mock_client(empty_choices=True)
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        result = await run_phase1("test", [])

    assert result["_fallback"] is True
    assert result["_fallback_reason"] == "response_content_error"


@pytest.mark.asyncio
async def test_null_message_content_returns_content_error(tmp_path, monkeypatch):
    """
    Failure Mode 7: choices[0].message.content is None.
    _fallback_reason == 'response_content_error'.
    """
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    client = make_mock_client(null_content=True)
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        result = await run_phase1("test", [])

    assert result["_fallback"] is True
    assert result["_fallback_reason"] == "response_content_error"


@pytest.mark.asyncio
async def test_content_error_does_not_retry(tmp_path, monkeypatch):
    """Empty content is non-recoverable — no sleep, generate called once."""
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    client = make_mock_client(empty_choices=True)
    sleep_mock = AsyncMock()
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        with patch("engine.phase1_runner.asyncio.sleep", sleep_mock):
            result = await run_phase1("test", [], max_retries=1)

    assert result["_fallback"] is True
    sleep_mock.assert_not_called()
    assert client.chat.completions.create.call_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# Happy path — valid response
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_success_returns_validated_output(tmp_path, monkeypatch):
    """
    Happy path: valid JSON from model → validate_phase1_output() called →
    _fallback=False, _fallback_reason="", all fields populated.
    """
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    client = make_mock_client(response_text=VALID_PHASE1_JSON)
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        result = await run_phase1("How much rice should I eat for diabetes?", [])

    assert result["_fallback"] is False
    assert result["_fallback_reason"] == ""
    assert result["intent"] == "healthy_eating"
    assert result["qds_score"] == 2
    assert result["context_sufficient"] is True
    assert result["profile_signals"]["medications_mentioned"] == ["metformin"]
    assert result["profile_signals"]["session_context"] == "self"
    assert result["mid_clarification_resolved"] is False


@pytest.mark.asyncio
async def test_success_no_failure_log_written(tmp_path, monkeypatch):
    """Successful call must NOT write to the failure log."""
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    log_path = tmp_path / "phase1_failures.jsonl"
    monkeypatch.setattr(runner, "LOG_PATH", log_path)
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    client = make_mock_client(response_text=VALID_PHASE1_JSON)
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        await run_phase1("test", [])

    assert not log_path.exists()


# ─────────────────────────────────────────────────────────────────────────────
# validate_phase1_output() overrides via run_phase1 integration
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_qds5_context_sufficient_override(tmp_path, monkeypatch):
    """
    QDS 5 must always be context_sufficient=True — even if model returns False.
    validate_phase1_output() enforces this.
    """
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    qds5_with_wrong_sufficient = json.dumps(
        {
            "intent": "healthy_coping",
            "qds_score": 5,
            "context_sufficient": False,  # wrong — validate_phase1_output must override
            "clarifying_questions": [
                {"text": "How long?", "format": "open", "options": []}
            ],
            "profile_signals": {
                "diabetes_type": "", "medications_mentioned": [], "insulin_user": False,
                "condition_flags": [], "complications_mentioned": [], "location_hint": "",
                "session_context": "self",
            },
            "mid_clarification_resolved": False,
        }
    )
    client = make_mock_client(response_text=qds5_with_wrong_sufficient)
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        result = await run_phase1("I feel like giving up on everything", [])

    assert result["_fallback"] is False
    assert result["qds_score"] == 5
    assert result["context_sufficient"] is True    # overridden
    assert result["clarifying_questions"] == []    # cleared by validator


@pytest.mark.asyncio
async def test_qds1_context_sufficient_override(tmp_path, monkeypatch):
    """QDS 1 must always be context_sufficient=True (general awareness)."""
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    qds1_wrong = json.dumps(
        {
            "intent": "general_dsmes",
            "qds_score": 1,
            "context_sufficient": False,  # wrong
            "clarifying_questions": [
                {"text": "When was your diagnosis?", "format": "open", "options": []}
            ],
            "profile_signals": {
                "diabetes_type": "", "medications_mentioned": [], "insulin_user": False,
                "condition_flags": [], "complications_mentioned": [], "location_hint": "",
                "session_context": "self",
            },
            "mid_clarification_resolved": False,
        }
    )
    client = make_mock_client(response_text=qds1_wrong)
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        result = await run_phase1("What is HbA1c?", [])

    assert result["context_sufficient"] is True


@pytest.mark.asyncio
async def test_escalation_only_always_sufficient(tmp_path, monkeypatch):
    """escalation_only intent must always be context_sufficient=True."""
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    escalation_json = json.dumps(
        {
            "intent": "escalation_only",
            "qds_score": 3,
            "context_sufficient": False,  # wrong
            "clarifying_questions": [
                {"text": "Which medicine?", "format": "buttons", "options": ["A", "B"]}
            ],
            "profile_signals": {
                "diabetes_type": "", "medications_mentioned": [], "insulin_user": False,
                "condition_flags": [], "complications_mentioned": [], "location_hint": "",
                "session_context": "self",
            },
            "mid_clarification_resolved": False,
        }
    )
    client = make_mock_client(response_text=escalation_json)
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        result = await run_phase1("Should I take more insulin?", [])

    assert result["intent"] == "escalation_only"
    assert result["context_sufficient"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Deep copy safety — PHASE1_FALLBACK must never be mutated
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fallback_deep_copy_no_mutation_across_calls(tmp_path, monkeypatch):
    """
    PHASE1_FALLBACK must be unchanged after any number of fallback calls.
    _make_fallback() must deep-copy the nested profile_signals dict.
    """
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")

    original = copy.deepcopy(PHASE1_FALLBACK)

    r1 = await run_phase1("message one", [], user_id="user-a")
    r2 = await run_phase1("message two", [], user_id="user-b")

    assert r1["_fallback"] is True
    assert r2["_fallback"] is True

    # PHASE1_FALLBACK itself must be completely unchanged
    assert PHASE1_FALLBACK == original
    assert PHASE1_FALLBACK["_fallback_reason"] == ""  # never stamped


@pytest.mark.asyncio
async def test_fallback_profile_signals_not_shared(tmp_path, monkeypatch):
    """
    Mutating profile_signals in one fallback result must not affect another.
    This proves the deep copy is effective on the nested dict.
    """
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.setattr(runner, "LOG_PATH", tmp_path / "phase1_failures.jsonl")

    r1 = await run_phase1("msg", [], user_id="u1")
    r2 = await run_phase1("msg", [], user_id="u2")

    # Mutate r1's nested dict
    r1["profile_signals"]["condition_flags"].append("ckd")

    # r2 and PHASE1_FALLBACK must be unaffected
    assert r2["profile_signals"]["condition_flags"] == []
    assert PHASE1_FALLBACK["profile_signals"]["condition_flags"] == []


# ─────────────────────────────────────────────────────────────────────────────
# _build_messages() — role mapping, system prompt, and turn window
# ─────────────────────────────────────────────────────────────────────────────


def test_build_messages_system_prompt_first():
    """System prompt is always the first message with role='system'."""
    messages = _build_messages([], "Hello", "You are a test educator.")
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are a test educator."


def test_build_messages_role_mapping():
    """patient → 'user', bot → 'assistant'. Current message always 'user' last."""
    turns = [
        {"role": "bot",     "content": "Is it burning or sharp?"},
        {"role": "patient", "content": "It is burning and tingling at night."},
    ]
    messages = _build_messages(turns, "My feet hurt.", "sys")

    # Index 0: system
    assert messages[0]["role"] == "system"
    # Index 1: bot → assistant
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "Is it burning or sharp?"
    # Index 2: patient → user
    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == "It is burning and tingling at night."
    # Index 3: current message → user (last)
    assert messages[3]["role"] == "user"
    assert messages[3]["content"] == "My feet hurt."


def test_build_messages_current_message_always_last():
    """current_message is always the final entry."""
    turns = [{"role": "bot", "content": "I see."}]
    messages = _build_messages(turns, "Current patient message here.", "sys")
    assert messages[-1]["content"] == "Current patient message here."
    assert messages[-1]["role"] == "user"


def test_build_messages_truncates_to_last_5_turns():
    """
    session_turns is capped at last 5 for context window efficiency.
    Total messages = 1 (system) + 5 (session) + 1 (current) = 7.
    """
    turns = [{"role": "patient", "content": f"turn {i}"} for i in range(10)]
    messages = _build_messages(turns, "current", "sys")

    # 1 system + 5 session + 1 current = 7
    assert len(messages) == 7
    # First session turn kept is index 5 (turns[-5:] = turns[5:10])
    assert messages[1]["content"] == "turn 5"
    assert messages[5]["content"] == "turn 9"


def test_build_messages_empty_session_turns():
    """Empty session_turns → system + current message only (2 messages)."""
    messages = _build_messages([], "First patient question ever.", "sys")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "First patient question ever."


def test_build_messages_unknown_role_becomes_user():
    """Unknown role string (neither 'bot' nor 'patient') maps to 'user' safely."""
    turns = [{"role": "system", "content": "some content"}]
    messages = _build_messages(turns, "current", "sys")
    assert messages[1]["role"] == "user"  # safe default


def test_build_messages_exactly_5_turns_no_truncation():
    """Exactly 5 turns — all included. Total = 1 (system) + 5 + 1 (current) = 7."""
    turns = [{"role": "patient", "content": f"msg {i}"} for i in range(5)]
    messages = _build_messages(turns, "current", "sys")
    assert len(messages) == 7


# ─────────────────────────────────────────────────────────────────────────────
# Failure log schema (PHASE1_CONTEXT_ENGINE_SPEC.md Item 4c)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_failure_log_written_with_correct_schema(tmp_path, monkeypatch):
    """
    On fallback, logs/phase1_failures.jsonl must contain all 7 required fields
    from PHASE1_CONTEXT_ENGINE_SPEC.md Item 4c.
    """
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    log_path = tmp_path / "phase1_failures.jsonl"
    monkeypatch.setattr(runner, "LOG_PATH", log_path)

    await run_phase1("My sugar is very high", [], user_id="patient-kerala-01")

    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert "timestamp"       in record
    assert "user_id"         in record
    assert "message_id"      in record
    assert "error_type"      in record
    assert "raw_output"      in record
    assert "fallback_applied" in record
    assert "attempt_count"   in record

    assert record["error_type"]      == "missing_api_key"
    assert record["user_id"]         == "patient-kerala-01"
    assert record["fallback_applied"] is True
    assert isinstance(record["attempt_count"], int)

    import uuid as _uuid
    _uuid.UUID(record["message_id"])  # raises ValueError if not a valid UUID


@pytest.mark.asyncio
async def test_failure_log_user_id_defaults_to_unknown(tmp_path, monkeypatch):
    """user_id='unknown' in log when not provided — never null."""
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    log_path = tmp_path / "phase1_failures.jsonl"
    monkeypatch.setattr(runner, "LOG_PATH", log_path)

    await run_phase1("test", [])  # no user_id argument

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["user_id"] == "unknown"


@pytest.mark.asyncio
async def test_failure_log_raw_output_truncated_at_2000(tmp_path, monkeypatch):
    """raw_output is truncated to first 2000 chars — prevents giant log entries."""
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-fake")
    log_path = tmp_path / "phase1_failures.jsonl"
    monkeypatch.setattr(runner, "LOG_PATH", log_path)
    monkeypatch.setattr(runner, "_system_prompt_text", DUMMY_SYSTEM_PROMPT)

    long_response = "x" * 5000
    client = make_mock_client(response_text=long_response)
    with patch("engine.phase1_runner.openai.AsyncOpenAI", return_value=client):
        await run_phase1("test", [])

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert len(record["raw_output"]) == 2000


@pytest.mark.asyncio
async def test_failure_log_multiple_failures_appended(tmp_path, monkeypatch):
    """Multiple fallbacks append separate JSONL lines — log is not overwritten."""
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    log_path = tmp_path / "phase1_failures.jsonl"
    monkeypatch.setattr(runner, "LOG_PATH", log_path)

    await run_phase1("first fail", [], user_id="u1")
    await run_phase1("second fail", [], user_id="u2")

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    records = [json.loads(line) for line in lines]
    assert records[0]["user_id"] == "u1"
    assert records[1]["user_id"] == "u2"


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────


def test_request_timeout_value():
    """REQUEST_TIMEOUT must be set — 8.0s for free-tier llama testing, 3.0s for production qwen."""
    assert runner.REQUEST_TIMEOUT > 0


def test_model_id_is_cerebras_qwen():
    """MODEL_ID must point to Cerebras qwen-3-235b production model."""
    assert runner.MODEL_ID == "qwen-3-235b-a22b-instruct-2507"


def test_cerebras_url_is_correct():
    """CEREBRAS_URL must point at the Cerebras API endpoint."""
    assert runner.CEREBRAS_URL == "https://api.cerebras.ai/v1"
