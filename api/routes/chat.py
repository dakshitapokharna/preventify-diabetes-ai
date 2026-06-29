"""
api/routes/chat.py — POST /chat SSE streaming endpoint

Two-phase streaming:
  Phase A — Status events while pipeline runs (cannot do true token streaming
             because check_constraints() needs the full response text first).
  Phase B — After constraint check passes, stream response word-by-word at ~30ms.

SSE event format (one per line):  data: <json>\n\n

Event types: status | chunk | clarify | done | error
"""

import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from api.rate_limiter import is_allowed
from api.audit_logger import write_audit_log
from api.session_manager import process_turn
from schemas.phase2_schema import PHASE2_CONSTRAINT_FALLBACK_TEXT
from engine.compare_runner import run_compare_stream
from engine.translator import translate_to_english

log = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Request schema
# ─────────────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message:    str
    user_id:    str
    session_id: str

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message cannot be empty")
        if len(v) > 2000:
            raise ValueError("message too long (max 2000 chars)")
        return v

    @field_validator("user_id", "session_id")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("id cannot be empty")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# SSE helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sse(event_type: str, **kwargs) -> str:
    """Format one SSE data line."""
    return f"data: {json.dumps({'type': event_type, **kwargs})}\n\n"


async def _stream_text(text: str, delay_ms: int = 30) -> AsyncGenerator[str, None]:
    """
    Stream response text word-by-word with a small delay.
    Groups short words into 1–3 word phrases to feel natural.
    """
    words = text.split()
    i = 0
    while i < len(words):
        # Emit 1–2 words per chunk
        chunk = " ".join(words[i:i+2])
        if i + 2 < len(words):
            chunk += " "
        yield _sse("chunk", text=chunk)
        i += 2
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)


# ─────────────────────────────────────────────────────────────────────────────
# Source detail builder for done.meta
# ─────────────────────────────────────────────────────────────────────────────

def _build_sources(phase2_output: dict) -> list:
    """
    Extract source details from phase2_output for the done.meta payload.
    Returns list of {source, section, grade} dicts.
    """
    if not phase2_output:
        return []
    chunks_detail = phase2_output.get("chunks_detail") or []
    sources = []
    for c in chunks_detail:
        # chunks_detail from phase2_runner uses keys: source, section, grade
        # (phase2_runner already maps section_title->section, grade_priority->grade)
        sources.append({
            "source":  c.get("source", ""),
            "section": c.get("section", ""),
            "grade":   c.get("grade", 0),
        })
    return sources


# ─────────────────────────────────────────────────────────────────────────────
# Main generator
# ─────────────────────────────────────────────────────────────────────────────

async def _chat_stream(req: ChatRequest, request: Request) -> AsyncGenerator[str, None]:
    """
    Full SSE generator for one patient message.
    Acquired DB connection is released when this generator returns.
    """
    app = request.app
    db_pool = app.state.db_pool

    async with db_pool.acquire() as conn:
        # ── Phase A: Status events ─────────────────────────────────────────────
        yield _sse("status", text="Looking up your question...")

        # ── Translate Malayalam input → English for pipeline ───────────────────
        english_message = translate_to_english(req.message)

        # ── Run full pipeline ──────────────────────────────────────────────────
        try:
            result, turn_number = await process_turn(
                message=english_message,
                user_id=req.user_id,
                session_id=req.session_id,
                db_conn=conn,
                db_conn_pool=db_pool,
                app_state=app.state,
            )
        except Exception as exc:
            log.exception("chat route: pipeline error user=%s — %s", req.user_id, exc)
            yield _sse("error", text="Something went wrong. Please try again.")
            yield _sse("done", meta={})
            return

        phase1  = result.get("phase1")  or {}
        phase2  = result.get("phase2")  or {}
        risk    = result.get("risk_tier", 0)
        response = result.get("response") or {}

        # ── Write audit log (await inline — conn still open, avoids race) ────────
        await write_audit_log(
            user_id=req.user_id,
            session_id=req.session_id,
            turn_number=turn_number,
            patient_message=req.message,
            result=result,
            db_conn=conn,
        )

        # ── Build done.meta ────────────────────────────────────────────────────
        meta = {
            "qds_score":           phase1.get("qds_score"),
            "intent":              phase1.get("intent"),
            "risk_tier":           risk,
            "sources":             _build_sources(phase2),
            "phase1_fallback":     bool(phase1.get("_fallback", False)),
            "phase2_fallback":     bool(phase2.get("_fallback", False)),
            "constraint_violation": bool(phase2.get("constraint_violation", False)),
            "query_cache_hit":     bool(phase2.get("query_cache_hit", False)),
            "session_turn_count":  turn_number,
            "timings":             phase2.get("timings") or {},
        }

        # ── Phase B: Route output by response type ─────────────────────────────

        # Case 1: Clarifying question
        if response.get("_clarifying"):
            cqs = response.get("_clarifying_questions") or []
            if cqs:
                cq = cqs[0]  # send first clarifying question
                # clarifying_questions from phase1_schema use "text" key (not "question")
                # SSE event uses "question" as the field name per API contract (Section 5)
                yield _sse(
                    "clarify",
                    question=cq.get("text", ""),
                    format=cq.get("format", "open"),
                    options=cq.get("options") or [],
                )
            else:
                # Fallback: treat as text response
                yield _sse("status", text="Generating response...")
                async for chunk in _stream_text(response.get("text") or ""):
                    yield chunk
            yield _sse("done", meta=meta)
            return

        # Case 2: Constraint violation — send safe message instead
        if phase2.get("constraint_violation"):
            safe = PHASE2_CONSTRAINT_FALLBACK_TEXT
            yield _sse("status", text="Generating response...")
            async for chunk in _stream_text(safe):
                yield chunk
            yield _sse("done", meta=meta)
            return

        # Case 3: Normal / fallback response text
        response_text = response.get("text") or ""
        if not response_text:
            response_text = "I'm sorry, I wasn't able to generate a response. Please try rephrasing your question."

        # Status hint about sources used (if Phase 2 ran)
        sources = _build_sources(phase2)
        if sources:
            source_names = list({s["source"] for s in sources})[:2]
            yield _sse("status", text=f"Found relevant sections from {', '.join(source_names)}...")
        yield _sse("status", text="Generating response...")

        async for chunk in _stream_text(response_text):
            yield chunk

        yield _sse("done", meta=meta)


# ─────────────────────────────────────────────────────────────────────────────
# Route
# ─────────────────────────────────────────────────────────────────────────────

class CompareRequest(BaseModel):
    message:    str
    user_id:    str
    session_id: str

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message cannot be empty")
        if len(v) > 2000:
            raise ValueError("message too long (max 2000 chars)")
        return v

    @field_validator("user_id", "session_id")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("id cannot be empty")
        return v


async def _compare_stream(body: CompareRequest, request: Request) -> AsyncGenerator[str, None]:
    """SSE generator for compare mode — runs full Phase 1 + RAG, then fans out to all models."""
    from api.session_manager import load_user_profile, load_session_turns
    app     = request.app
    db_pool = app.state.db_pool

    async with db_pool.acquire() as conn:
        profile       = await load_user_profile(body.user_id, conn)
        session_turns = await load_session_turns(body.user_id, body.session_id, conn)

        if profile is None:
            profile = {}
        if not profile.get("location_hint"):
            profile["location_hint"] = "Kerala"

        short_memory = profile.get("short_memory") or ""

        async for event in run_compare_stream(
            message=body.message,
            session_turns=session_turns,
            profile=profile,
            short_memory=short_memory,
            db_conn=conn,
            embedder=app.state.embedder,
            reranker=app.state.reranker,
            user_id=body.user_id,
        ):
            yield event


@router.post("/compare")
async def compare_endpoint(body: CompareRequest, request: Request):
    """
    POST /compare — runs Phase 1 + RAG, then fans the enriched prompt out to all
    available models in parallel and streams results via SSE as each completes.

    SSE events: status | compare_start | model_result | compare_done | error
    """
    return StreamingResponse(
        _compare_stream(body, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat")
async def chat_endpoint(body: ChatRequest, request: Request):
    """
    POST /chat — SSE streaming response.
    Content-Type: text/event-stream
    """
    # Rate limit check (sync, fast)
    if not is_allowed(body.user_id):
        async def _rate_limited():
            yield _sse("error", text="You've sent too many messages. Please wait a few minutes.")
            yield _sse("done", meta={})

        return StreamingResponse(
            _rate_limited(),
            media_type="text/event-stream",
            status_code=429,
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return StreamingResponse(
        _chat_stream(body, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
