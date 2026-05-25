"""
api/session_manager.py — Per-turn session management

Responsibilities per turn:
  1. Load user profile from DB (or None for new users)
  2. Load last 5 session turns for this session_id
  3. Run the engine pipeline via handle_phase1()
  4. Save this turn (patient message + bot response) to session_turns
  5. Check session end (10 patient turns) → compress_memory() as background task

Called by the /chat route handler once per patient message.
"""

import asyncio
import logging
from typing import Optional, Tuple

from engine.phase1 import handle_phase1
from api.memory_compressor import compress_session, update_short_memory

log = logging.getLogger(__name__)

SESSION_TURN_LIMIT = 10   # patient turns per session before memory compression


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

async def load_user_profile(user_id: str, db_conn) -> Optional[dict]:
    """
    Load the user row from `users` table. Returns None if new user.
    Never raises — returns None on DB error (treated as new user).
    """
    try:
        row = await db_conn.fetchrow(
            """
            SELECT user_id, diabetes_type, condition_flags, medications_mentioned,
                   insulin_user, complications_mentioned, highest_qds_ever,
                   location_hint, short_memory, lifetime_score, lead_status,
                   consent_status, total_sessions
            FROM users
            WHERE user_id = $1
            """,
            user_id,
        )
        if row is None:
            return None
        return dict(row)
    except Exception as exc:
        log.error("session_manager: load_user_profile failed user=%s — %s", user_id, exc)
        return None


async def load_session_turns(user_id: str, session_id: str, db_conn) -> list:
    """
    Load the last 5 turns for this session as [{"role": ..., "content": ...}].
    Returns [] if none found or on error.
    """
    try:
        rows = await db_conn.fetch(
            """
            SELECT role, content
            FROM session_turns
            WHERE user_id = $1 AND session_id = $2
            ORDER BY turn_number ASC
            """,
            user_id,
            session_id,
        )
        # Return last 5 turns (last 2.5 exchanges)
        turns = [{"role": r["role"], "content": r["content"]} for r in rows]
        return turns[-5:] if len(turns) > 5 else turns
    except Exception as exc:
        log.error("session_manager: load_session_turns failed user=%s session=%s — %s",
                  user_id, session_id, exc)
        return []


async def _count_patient_turns(user_id: str, session_id: str, db_conn) -> int:
    """Count patient turns in this session (not including current)."""
    try:
        result = await db_conn.fetchval(
            """
            SELECT COUNT(*) FROM session_turns
            WHERE user_id = $1 AND session_id = $2 AND role = 'patient'
            """,
            user_id,
            session_id,
        )
        return int(result or 0)
    except Exception as exc:
        log.error("session_manager: _count_patient_turns failed — %s", exc)
        return 0


async def save_turn(
    user_id: str,
    session_id: str,
    turn_number: int,
    patient_message: str,
    bot_response: str,
    qds_score: Optional[int],
    risk_tier: int,
    db_conn,
) -> None:
    """
    Persist both sides of this turn to session_turns.
    Two rows: patient turn + bot turn.
    Never raises.
    """
    try:
        # Patient turn
        await db_conn.execute(
            """
            INSERT INTO session_turns
                (user_id, session_id, turn_number, role, content, qds_score, risk_tier)
            VALUES ($1, $2, $3, 'patient', $4, $5, $6)
            ON CONFLICT DO NOTHING
            """,
            user_id, session_id, turn_number * 2 - 1,
            patient_message, qds_score, risk_tier,
        )
        # Bot turn
        await db_conn.execute(
            """
            INSERT INTO session_turns
                (user_id, session_id, turn_number, role, content)
            VALUES ($1, $2, $3, 'bot', $4)
            ON CONFLICT DO NOTHING
            """,
            user_id, session_id, turn_number * 2,
            bot_response or "",
        )
        # Increment total_messages on user row (2 per turn)
        await db_conn.execute(
            """
            UPDATE users SET total_messages = total_messages + 1
            WHERE user_id = $1
            """,
            user_id,
        )
    except Exception as exc:
        log.error("session_manager: save_turn failed user=%s session=%s — %s",
                  user_id, session_id, exc)


async def _load_all_session_turns(user_id: str, session_id: str, db_conn) -> list:
    """Load ALL turns for memory compression (not capped at 5)."""
    try:
        rows = await db_conn.fetch(
            """
            SELECT role, content FROM session_turns
            WHERE user_id = $1 AND session_id = $2
            ORDER BY turn_number ASC
            """,
            user_id, session_id,
        )
        return [{"role": r["role"], "content": r["content"]} for r in rows]
    except Exception as exc:
        log.error("session_manager: _load_all_session_turns failed — %s", exc)
        return []


async def _delete_session_turns(user_id: str, session_id: str, db_conn) -> None:
    """Delete all rows for this session after compression."""
    try:
        await db_conn.execute(
            "DELETE FROM session_turns WHERE user_id = $1 AND session_id = $2",
            user_id, session_id,
        )
    except Exception as exc:
        log.error("session_manager: _delete_session_turns failed — %s", exc)


async def end_session(user_id: str, session_id: str, db_conn_pool, flash_client=None) -> None:
    """
    Called as a background task after the 10th patient turn.
    1. Load all turns → 2. Compress → 3. Update short_memory → 4. Delete turns
    """
    log.info("session_manager: ending session user=%s session=%s", user_id, session_id)
    async with db_conn_pool.acquire() as conn:
        turns = await _load_all_session_turns(user_id, session_id, conn)
        summary = await compress_session(turns)
        if summary:
            await update_short_memory(user_id, summary, conn)
        await _delete_session_turns(user_id, session_id, conn)
    log.info("session_manager: session ended user=%s — summary %d chars", user_id, len(summary or ""))


# ─────────────────────────────────────────────────────────────────────────────
# Main per-turn function
# ─────────────────────────────────────────────────────────────────────────────

async def process_turn(
    *,
    message: str,
    user_id: str,
    session_id: str,
    db_conn,
    db_conn_pool,
    app_state,
) -> Tuple[dict, int]:
    """
    Execute one full patient turn through the pipeline.

    Args:
        message:       Patient message (English).
        user_id:       UUID (testing) or WhatsApp number (production).
        session_id:    UUID per browser session.
        db_conn:       asyncpg Connection (acquired from pool by the route handler).
        db_conn_pool:  The full asyncpg pool (used for background end_session task).
        app_state:     FastAPI app.state — holds .embedder and .reranker.

    Returns:
        (result dict from handle_phase1(), turn_number)
    """
    # 1. Load profile then session turns — sequential on the same connection.
    # asyncpg does not allow concurrent queries on a single connection;
    # gather() would trigger "another operation is in progress".
    profile      = await load_user_profile(user_id, db_conn)
    session_turns = await load_session_turns(user_id, session_id, db_conn)

    # Default location to Kerala for new users (no profile yet) and existing users
    # with no location on record. Kerala is the primary patient population.
    if profile is None:
        profile = {}
    if not profile.get("location_hint"):
        profile["location_hint"] = "Kerala"

    # 2. Run pipeline
    result = await handle_phase1(
        message=message,
        session_turns=session_turns,
        user_profile=profile,
        user_id=user_id,
        db_conn=db_conn,
        embedder=app_state.embedder,
        reranker=app_state.reranker,
    )

    # 3. Count patient turns (after this one)
    prior_patient_turns = await _count_patient_turns(user_id, session_id, db_conn)
    turn_number = prior_patient_turns + 1

    # 4. Save turn to DB
    response = result.get("response") or {}
    bot_response_text = response.get("text") or ""
    phase1 = result.get("phase1") or {}

    await save_turn(
        user_id=user_id,
        session_id=session_id,
        turn_number=turn_number,
        patient_message=message,
        bot_response=bot_response_text,
        qds_score=phase1.get("qds_score"),
        risk_tier=result.get("risk_tier", 0),
        db_conn=db_conn,
    )

    # 5. Session end check
    if turn_number >= SESSION_TURN_LIMIT:
        asyncio.create_task(
            end_session(user_id, session_id, db_conn_pool)
        )
        log.info("session_manager: session end triggered user=%s turn=%d", user_id, turn_number)

    return result, turn_number
