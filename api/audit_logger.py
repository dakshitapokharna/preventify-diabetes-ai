"""
api/audit_logger.py — SaMD audit trail writer

Writes one row to conversation_audit_log per turn.
Called AFTER handle_phase1() returns — never before.
Never raises — failures are logged to stderr only (audit failures must not
break the patient response).

See schemas/conversation_audit_log.sql for the full table definition.
"""

import logging
import uuid
from typing import Optional

log = logging.getLogger(__name__)


def _determine_response_type(phase1_output: dict, phase2_output: Optional[dict], risk_tier: int) -> str:
    """
    Map pipeline outputs → response_type enum value.

    Priority order (highest first):
      1. Phase 1 fallback fired
      2. Risk Tier 3/4
      3. Clarifying question (context_sufficient=False)
      4. Phase 2 fallback fired
      5. Normal phase2_response
    """
    if phase1_output.get("_fallback"):
        return "phase1_fallback"
    if risk_tier >= 3:
        return "risk_escalation"
    if not phase1_output.get("context_sufficient", True):
        return "clarifying_question"
    if phase2_output and phase2_output.get("_fallback"):
        return "phase2_fallback"
    return "phase2_response"


async def write_audit_log(
    *,
    user_id: str,
    session_id: str,
    turn_number: int,
    patient_message: str,
    result: dict,                # full dict returned by handle_phase1()
    db_conn,                     # asyncpg Connection
) -> str:
    """
    Write one row to conversation_audit_log.

    Args:
        user_id:          Patient identifier.
        session_id:       UUID per session.
        turn_number:      Sequential turn count within this session.
        patient_message:  Raw patient message (English, post-translation).
        result:           Full dict from handle_phase1() — keys: phase1, phase2, risk_tier, response.
        db_conn:          asyncpg Connection.

    Returns:
        message_id (UUID str) — echoed back for the done.meta payload.

    Never raises — logs errors silently.
    """
    message_id = str(uuid.uuid4())

    phase1 = result.get("phase1") or {}
    phase2 = result.get("phase2") or {}
    risk_tier = result.get("risk_tier", 0)
    response = result.get("response") or {}

    response_type = _determine_response_type(phase1, phase2 or None, risk_tier)

    # Bot response text — None for Tier 4 emergency bypass
    bot_response: Optional[str] = response.get("text")

    # Phase 2 fields — safe defaults if Phase 2 was skipped
    chunks_used           = phase2.get("chunks_used") or []
    condition_flags_active = phase2.get("condition_flags_active") or []
    query_cache_hit       = phase2.get("query_cache_hit")
    reranker_scores       = phase2.get("reranker_scores") or []
    phase2_fallback       = bool(phase2.get("_fallback", False))
    phase2_fallback_reason = phase2.get("_fallback_reason")
    constraint_violation  = bool(phase2.get("constraint_violation", False))
    constraint_violations = phase2.get("constraint_violations") or []

    # Tier 3 subtype
    tier_3_subtype: Optional[str] = result.get("tier_3_subtype")

    try:
        await db_conn.execute(
            """
            INSERT INTO conversation_audit_log (
                user_id, session_id, message_id, turn_number,
                patient_message, bot_response, response_type,
                qds_score, intent, phase1_fallback,
                risk_tier, tier_3_subtype,
                chunks_used, condition_flags_active, query_cache_hit,
                reranker_scores, phase2_fallback, phase2_fallback_reason,
                constraint_violation, constraint_violations
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6, $7,
                $8, $9, $10,
                $11, $12,
                $13, $14, $15,
                $16, $17, $18,
                $19, $20
            )
            """,
            user_id,
            session_id,
            message_id,
            turn_number,
            patient_message,
            bot_response,
            response_type,
            phase1.get("qds_score"),
            phase1.get("intent"),
            bool(phase1.get("_fallback", False)),
            risk_tier,
            tier_3_subtype,
            chunks_used,
            condition_flags_active,
            query_cache_hit,
            reranker_scores,
            phase2_fallback,
            phase2_fallback_reason,
            constraint_violation,
            constraint_violations,
        )
        log.debug("audit_logger: wrote row message_id=%s type=%s", message_id, response_type)
    except Exception as exc:
        # Audit failures must never break the patient response — log and continue.
        log.error("audit_logger: FAILED to write audit row — %s: %s", type(exc).__name__, exc)

    return message_id
