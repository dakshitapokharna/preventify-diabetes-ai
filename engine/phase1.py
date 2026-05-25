"""
engine/phase1.py — Phase 1 Orchestrator

Wires together:
  - engine/phase1_runner.py   (Phase 1 — Context Engine)
  - engine/signal_writer.py   (profile signal → Neon DB)
  - engine/phase2_runner.py   (Phase 2 — RAG pipeline)
  - engine/response_formatter.py  (risk nudge merge)

into a single `async def handle_phase1(...)` callable for the session manager.

Turn execution order:
  1. run_phase1()             — Context Engine: intent, QDS, profile signals, clarification flag
  2. write_profile_signals()  — fire-and-forget DB write; never blocks the patient response
  3. run_phase2()             — RAG pipeline (only when context_sufficient=True and
                                intent != escalation_only and risk_tier != 4)
  4. build_response()         — merge Phase 2 text + risk tier nudge
  5. Return structured result

Risk Engine:
  Not yet built (B4 blocker). risk_tier defaults to 0 (education only).
  When the Risk Engine is added, it should run in parallel with Phase 1
  (asyncio.gather) and its result passed as `risk_tier` to handle_phase1().

Usage:
    from engine.phase1 import handle_phase1, load_ml_models

    # At server startup (load once — models are ~1.5 GB total):
    embedder, reranker = load_ml_models(settings)

    # Per patient message turn:
    result = await handle_phase1(
        message="Can I eat rice with T2DM?",
        session_turns=[{"role": "patient", "content": "..."}, ...],
        user_profile={"diabetes_type": "T2DM", "condition_flags": [], ...},
        user_id="uuid-or-whatsapp-number",
        db_conn=asyncpg_connection,   # must have register_vector() called on it
        embedder=embedder,
        reranker=reranker,
    )

    result["response"]["text"]        → patient-facing response (nudge merged)
    result["phase1"]["intent"]         → intent for analytics
    result["phase1"]["qds_score"]      → QDS for lead scoring
    result["phase2"]["chunks_used"]    → chunk IDs for SaMD audit trail (or None if P2 skipped)
    result["risk_tier"]                → 0 until Risk Engine wired in

Environment:
    OPENROUTER_API_KEY   — required by phase1_runner.py and phase2_runner.py
    DATABASE_URL         — required for asyncpg connection (caller's responsibility)
"""

import asyncio
import logging
from typing import Optional

from engine.phase1_runner import run_phase1
from engine.phase2_runner import run_phase2, load_reranker
from engine.response_formatter import build_response
from engine.signal_writer import write_profile_signals

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Startup helper — load ML models once at server init
# ─────────────────────────────────────────────────────────────────────────────

def load_ml_models(settings):
    """
    Load the embedding model (bge-large-en-v1.5) and reranker (bge-reranker-large)
    at server startup. Both are sync PyTorch models — heavy, load once only.

    Args:
        settings: Config object with `embedding_model` and `reranker_model` string
                  attributes. See config/settings.py.

    Returns:
        (embedder, reranker) — pass both to every handle_phase1() call.

    Example:
        from config.settings import settings
        embedder, reranker = load_ml_models(settings)
    """
    from ingestion.embedder.embed import load_model as load_embedder
    embedder = load_embedder(settings.embedding_model)
    reranker  = load_reranker(settings.reranker_model)
    log.info(
        "phase1_orchestrator: ML models loaded — embedder=%s reranker=%s",
        settings.embedding_model, settings.reranker_model,
    )
    return embedder, reranker


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

async def handle_phase1(
    message: str,
    session_turns: list,
    user_profile: Optional[dict],
    user_id: str,
    db_conn,
    embedder,
    reranker,
    risk_tier: int = 0,
    tier_3_subtype: Optional[str] = None,
) -> dict:
    """
    Process one patient message through the full Phase 1 → Phase 2 pipeline.

    Always returns a structured result dict — never raises. Any internal failure
    is handled by the individual components (phase1_runner, phase2_runner) which
    return safe fallback dicts rather than raising.

    Args:
        message:          Patient's current message in English (post-translation).
        session_turns:    Prior turns as [{"role": "patient"/"bot", "content": "..."}].
                          Does NOT include current message. Last 5 used.
        user_profile:     User profile dict from DB, or None for new users.
                          Keys used: diabetes_type, condition_flags, medications_mentioned,
                          complications_mentioned, location_hint, short_memory.
        user_id:          Patient identifier — UUID in testing, WhatsApp number in production.
        db_conn:          asyncpg Connection. Must have register_vector() called on it.
        embedder:         SentenceTransformer instance (bge-large-en-v1.5). Load at startup.
        reranker:         FlagReranker instance (bge-reranker-large). Load at startup.
        risk_tier:        Risk Engine output 0–4. Defaults to 0 (education only) until
                          the Risk Engine is built. When built, run it in parallel with
                          Phase 1 via asyncio.gather() and pass the result here.
        tier_3_subtype:   "foot_wound" | "high_bg" | "hypoglycemia" when risk_tier == 3.

    Returns:
        dict with keys:
            "phase1"     — validated Phase 1 output dict
                           Keys: intent, qds_score, context_sufficient, clarifying_questions,
                                 profile_signals, mid_clarification_resolved, _fallback,
                                 _fallback_reason
            "phase2"     — Phase 2 output dict, or None if Phase 2 was skipped
                           Keys: text, chunks_used, condition_flags_active, query_cache_hit,
                                 reranker_scores, _fallback, _fallback_reason
            "risk_tier"  — risk tier applied this turn
            "response"   — final patient-facing response dict from build_response()
                           Keys: text, risk_tier, intent, qds_score, _clarifying
                           Additional key when _clarifying=True: _clarifying_questions
    """
    log.debug(
        "phase1_orchestrator: START user=%s message=%r",
        user_id, message[:80],
    )

    # ── Step 1: Run Phase 1 (Context Engine) ──────────────────────────────────
    # Classifies intent, assigns QDS, extracts profile signals, detects if a
    # clarifying question is needed, and flags mid-clarification turns.
    # Never raises — returns PHASE1_FALLBACK on any failure.
    phase1_output = await run_phase1(
        current_message=message,
        session_turns=session_turns,
        user_id=user_id,
    )
    log.debug(
        "phase1_orchestrator: phase1 done — intent=%s qds=%d sufficient=%s fallback=%s",
        phase1_output.get("intent"),
        phase1_output.get("qds_score", 0),
        phase1_output.get("context_sufficient"),
        phase1_output.get("_fallback"),
    )

    # ── Step 1b: Deterministic mid-clarification loop guard ───────────────────
    # If the last bot turn was a clarifying question (ends with "?") and Phase 1
    # returned context_sufficient=False again, force-resolve it.
    # This prevents infinite clarification loops when the model (qwen-3-235b) fails
    # to detect mid_clarification_resolved on its own — e.g. patient selects
    # "Not sure of the name" and the model incorrectly asks the same question again.
    # Rule: NEVER ask a second round of clarifying questions — prompt spec §edge_cases.
    if (
        not phase1_output.get("context_sufficient", True)
        and not phase1_output.get("mid_clarification_resolved", False)
        and not phase1_output.get("_fallback", False)
    ):
        last_bot_msgs = [t for t in session_turns if t.get("role") == "bot"]
        if last_bot_msgs:
            last_bot_text = last_bot_msgs[-1].get("content", "").strip()
            if last_bot_text.endswith("?"):
                # Last bot turn was a clarifying question — patient has already answered it.
                # Force resolution so Phase 2 runs with whatever context we have.
                phase1_output["context_sufficient"] = True
                phase1_output["mid_clarification_resolved"] = True
                phase1_output["clarifying_questions"] = []
                log.info(
                    "phase1_orchestrator: loop guard fired — forced mid-clarification "
                    "resolution for user=%s (last bot msg ended with '?')",
                    user_id,
                )

    # ── Step 2: Write profile signals (sequential, same connection) ───────────
    # Run sequentially — asyncpg does not allow concurrent queries on one
    # connection, and Phase 2 also uses db_conn. signal_writer never raises.
    await write_profile_signals(
        user_id=user_id,
        signals=phase1_output["profile_signals"],
        conn=db_conn,
        highest_qds=phase1_output.get("qds_score"),
    )

    # ── Step 3: Run Phase 2 RAG pipeline (conditional) ────────────────────────
    # Phase 2 runs only when ALL three conditions hold:
    #   a) context_sufficient=True — Phase 1 has enough context for a full answer
    #   b) intent != escalation_only — escalations go directly to Risk Engine / response formatter
    #   c) risk_tier != 4 — Tier 4 emergencies bypass the full pipeline
    phase2_output = None
    should_run_phase2 = (
        phase1_output.get("context_sufficient", True)
        and phase1_output.get("intent") != "escalation_only"
        and risk_tier != 4
    )

    if should_run_phase2:
        # short_memory is a ~100-token compressed profile string written at end of every
        # previous session by the memory compressor. Empty string "" for new users.
        short_memory = (user_profile or {}).get("short_memory", "")

        phase2_output = await run_phase2(
            current_message=message,
            session_turns=session_turns,
            phase1_output=phase1_output,
            profile=user_profile,
            short_memory=short_memory,
            db_conn=db_conn,
            embedder=embedder,
            reranker=reranker,
            user_id=user_id,
        )
        log.debug(
            "phase1_orchestrator: phase2 done — fallback=%s chars=%d cache_hit=%s",
            phase2_output.get("_fallback"),
            len(phase2_output.get("text") or ""),
            phase2_output.get("query_cache_hit"),
        )
    else:
        log.debug(
            "phase1_orchestrator: phase2 skipped — sufficient=%s intent=%s risk_tier=%d",
            phase1_output.get("context_sufficient"),
            phase1_output.get("intent"),
            risk_tier,
        )

    # ── Step 4: Build final patient-facing response ────────────────────────────
    # Merges Phase 2 text (if any) with the risk nudge. Handles:
    #   - context_sufficient=False → formats clarifying question
    #   - risk_tier 1/2 → nudge appended after response
    #   - risk_tier 3 → nudge prepended (urgency first)
    #   - risk_tier 4 → returns EMERGENCY_STUB (Risk Engine replaces it)
    phase2_text = (phase2_output.get("text") or None) if phase2_output else None
    response = build_response(
        phase1_output=phase1_output,
        risk_tier=risk_tier,
        tier_3_subtype=tier_3_subtype,
        phase2_text=phase2_text,
    )

    log.debug(
        "phase1_orchestrator: DONE user=%s risk=%d clarifying=%s intent=%s qds=%d",
        user_id, risk_tier,
        response.get("_clarifying", False),
        response.get("intent"),
        response.get("qds_score", 0),
    )

    # ── Step 5: Return structured turn result ──────────────────────────────────
    return {
        "phase1":          phase1_output,
        "phase2":          phase2_output,
        "risk_tier":       risk_tier,
        "tier_3_subtype":  tier_3_subtype,   # None until Risk Engine wired in; needed by audit_logger
        "response":        response,
    }
