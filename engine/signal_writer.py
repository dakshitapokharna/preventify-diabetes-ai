"""
signal_writer.py — Phase 1 profile signal → Neon DB writer

Called after every Phase 1 run, regardless of whether context_sufficient is True or False.
Applies merge rules per field — never blindly overwrites stored values.

Merge rules (one per field):
  diabetes_type       → one-way upgrade only (null → suspected → prediabetes → T1/T2/GDM)
  medications_mentioned → append only, no duplicates, controlled vocab only
  insulin_user        → latch True — once True is stored, never resets to False
  condition_flags     → append only, permanent, controlled vocab only
  complications_mentioned → append only, no duplicates, controlled vocab only
  location_hint       → overwrite if new value is non-empty and stored is empty
                        (specificity heuristic: len(new) >= len(stored) → overwrite)
  session_context     → NOT written to DB (session memory only)

Usage:
    from engine.signal_writer import write_profile_signals
    await write_profile_signals(user_id, phase1_output["profile_signals"], conn)
"""

import asyncio
import logging
from typing import Optional

import psycopg2.extras  # for execute_values
import asyncpg          # async Postgres driver for the main pipeline

from schemas.phase1_schema import (
    MEDICATION_VOCABULARY,
    COMPLICATION_VOCABULARY,
    CONDITION_FLAGS,
)

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# diabetes_type upgrade path — ordered from least to most specific
# Phase 1 may output a less specific value than what is stored.
# The writer only upgrades, never downgrades.
# ─────────────────────────────────────────────────────────────────────────────
DIABETES_TYPE_UPGRADE_ORDER = {
    "":           0,
    "suspected":  1,
    "prediabetes": 2,
    "T1DM":       3,
    "T2DM":       3,   # T1DM and T2DM are at the same level — once set, never replaced by each other
    "GDM":        3,
}

SENSITIVE_COMPLICATIONS = {"erectile_dysfunction_mentioned"}
# Sensitive complications are stored in the DB but NEVER surfaced in patient responses.
# The response generator must check for these and suppress any mention.


def _upgrade_diabetes_type(stored: Optional[str], incoming: str) -> str:
    """
    Return whichever diabetes_type value is more specific.
    Never downgrades a stored value.

    Examples:
        stored=None,        incoming="suspected"   → "suspected"
        stored="suspected", incoming="T2DM"        → "T2DM"
        stored="T2DM",      incoming="suspected"   → "T2DM"   (no downgrade)
        stored="T1DM",      incoming="T2DM"        → "T1DM"   (same level — keep stored)
        stored="T2DM",      incoming=""            → "T2DM"   (no downgrade)
    """
    stored = stored or ""
    incoming = incoming or ""
    stored_rank = DIABETES_TYPE_UPGRADE_ORDER.get(stored, 0)
    incoming_rank = DIABETES_TYPE_UPGRADE_ORDER.get(incoming, 0)

    if incoming_rank > stored_rank:
        return incoming
    return stored


def _merge_array(stored: list, incoming: list, vocabulary: list) -> list:
    """
    Append-only merge. Only accept values in the controlled vocabulary.
    Returns deduplicated list preserving insertion order.
    """
    allowed = set(vocabulary)
    merged = list(stored)  # copy
    stored_set = set(stored)
    for item in incoming:
        if item in allowed and item not in stored_set:
            merged.append(item)
            stored_set.add(item)
    return merged


def _merge_location(stored: str, incoming: str) -> str:
    """
    Overwrite stored location if incoming is non-empty AND
    at least as specific as stored (heuristic: len comparison).
    Empty string always loses.

    Examples:
        stored="",         incoming="Thrissur"    → "Thrissur"
        stored="Kerala",   incoming="Thrissur"    → "Thrissur"  (more specific)
        stored="Thrissur", incoming="Kerala"      → "Thrissur"  (stored is more specific)
        stored="Thrissur", incoming="Chalakudy"   → "Chalakudy" (same length, incoming wins)
        stored="Chalakudy", incoming=""           → "Chalakudy" (incoming empty, keep stored)
    """
    if not incoming:
        return stored
    if not stored:
        return incoming
    if len(incoming) >= len(stored):
        return incoming
    return stored


# ─────────────────────────────────────────────────────────────────────────────
# Main writer — asyncpg version (used in async pipeline)
# ─────────────────────────────────────────────────────────────────────────────

async def write_profile_signals(
    user_id: str,
    signals: dict,
    conn,  # asyncpg Connection
    highest_qds: Optional[int] = None,
    current_qds: Optional[int] = None,
) -> None:
    """
    Write Phase 1 profile_signals to the users table, applying all merge rules.

    Args:
        user_id:     Patient identifier (UUID in testing, WhatsApp number in production)
        signals:     The profile_signals dict from Phase 1 output (already validated)
        conn:        asyncpg connection (from the connection pool)
        highest_qds: If provided, also update highest_qds_ever if this value is higher

    Raises:
        Does not raise — logs errors and continues. Signal write failure must never
        block the patient from getting a response.
    """
    try:
        # Fetch current stored values for merge
        row = await conn.fetchrow(
            """
            SELECT diabetes_type, medications_mentioned, insulin_user,
                   condition_flags, complications_mentioned, location_hint,
                   highest_qds_ever
            FROM users
            WHERE user_id = $1
            """,
            user_id,
        )

        if row is None:
            # New user — create the row first
            await conn.execute(
                """
                INSERT INTO users (user_id)
                VALUES ($1)
                ON CONFLICT (user_id) DO NOTHING
                """,
                user_id,
            )
            # Re-fetch after insert (defaults will be in place)
            row = await conn.fetchrow(
                "SELECT diabetes_type, medications_mentioned, insulin_user, "
                "condition_flags, complications_mentioned, location_hint, highest_qds_ever "
                "FROM users WHERE user_id = $1",
                user_id,
            )

        # Apply merge rules
        new_diabetes_type = _upgrade_diabetes_type(
            stored=row["diabetes_type"],
            incoming=signals.get("diabetes_type", ""),
        )

        new_medications = _merge_array(
            stored=list(row["medications_mentioned"] or []),
            incoming=signals.get("medications_mentioned", []),
            vocabulary=MEDICATION_VOCABULARY,
        )

        new_insulin_user = bool(row["insulin_user"]) or bool(signals.get("insulin_user", False))

        new_condition_flags = _merge_array(
            stored=list(row["condition_flags"] or []),
            incoming=signals.get("condition_flags", []),
            vocabulary=CONDITION_FLAGS,
        )

        new_complications = _merge_array(
            stored=list(row["complications_mentioned"] or []),
            incoming=signals.get("complications_mentioned", []),
            vocabulary=COMPLICATION_VOCABULARY,  # includes erectile_dysfunction_mentioned
        )

        new_location = _merge_location(
            stored=row["location_hint"] or "",
            incoming=signals.get("location_hint", ""),
        )

        new_highest_qds = row["highest_qds_ever"] or 0
        if highest_qds and highest_qds > new_highest_qds:
            new_highest_qds = highest_qds

        # session_context is intentionally NOT written to DB
        # It is used only within the current session to frame Phase 2 responses.

        await conn.execute(
            """
            UPDATE users SET
                diabetes_type           = $2,
                medications_mentioned   = $3,
                insulin_user            = $4,
                condition_flags         = $5,
                complications_mentioned = $6,
                location_hint           = $7,
                highest_qds_ever        = $8,
                lifetime_score          = LEAST(lifetime_score + $9, 100.0)
            WHERE user_id = $1
            """,
            user_id,
            new_diabetes_type,
            new_medications,
            new_insulin_user,
            new_condition_flags,
            new_complications,
            new_location,
            new_highest_qds,
            float(current_qds or 0),
        )

        log.debug(
            "signal_writer: user=%s diabetes=%s flags=%s meds=%s complications=%s",
            user_id, new_diabetes_type, new_condition_flags,
            new_medications, new_complications,
        )

    except Exception as exc:
        # Signal write failure must never block the patient from getting a response.
        # Log and continue — the next turn will re-detect and re-write any missed signals.
        log.error(
            "signal_writer: FAILED for user=%s error=%s signals=%s",
            user_id, exc, signals,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Synchronous version — used in tests and one-off scripts
# ─────────────────────────────────────────────────────────────────────────────

def write_profile_signals_sync(
    user_id: str,
    signals: dict,
    conn,  # psycopg2 connection
    highest_qds: Optional[int] = None,
) -> None:
    """
    Synchronous version of write_profile_signals for use in tests.
    Same merge rules — psycopg2 instead of asyncpg.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT diabetes_type, medications_mentioned, insulin_user, "
                "condition_flags, complications_mentioned, location_hint, highest_qds_ever "
                "FROM users WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()

            if row is None:
                cur.execute(
                    "INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
                    (user_id,),
                )
                conn.commit()
                cur.execute(
                    "SELECT diabetes_type, medications_mentioned, insulin_user, "
                    "condition_flags, complications_mentioned, location_hint, highest_qds_ever "
                    "FROM users WHERE user_id = %s",
                    (user_id,),
                )
                row = cur.fetchone()

            (
                stored_dt, stored_meds, stored_insulin,
                stored_flags, stored_complications, stored_location,
                stored_qds,
            ) = row

            new_diabetes_type = _upgrade_diabetes_type(stored_dt, signals.get("diabetes_type", ""))
            new_medications   = _merge_array(stored_meds or [], signals.get("medications_mentioned", []), MEDICATION_VOCABULARY)
            new_insulin_user  = bool(stored_insulin) or bool(signals.get("insulin_user", False))
            new_flags         = _merge_array(stored_flags or [], signals.get("condition_flags", []), CONDITION_FLAGS)
            new_complications = _merge_array(stored_complications or [], signals.get("complications_mentioned", []), COMPLICATION_VOCABULARY)
            new_location      = _merge_location(stored_location or "", signals.get("location_hint", ""))
            new_highest_qds   = stored_qds or 0
            if highest_qds and highest_qds > new_highest_qds:
                new_highest_qds = highest_qds

            cur.execute(
                """
                UPDATE users SET
                    diabetes_type           = %s,
                    medications_mentioned   = %s,
                    insulin_user            = %s,
                    condition_flags         = %s,
                    complications_mentioned = %s,
                    location_hint           = %s,
                    highest_qds_ever        = %s
                WHERE user_id = %s
                """,
                (
                    new_diabetes_type, new_medications, new_insulin_user,
                    new_flags, new_complications, new_location, new_highest_qds,
                    user_id,
                ),
            )
            conn.commit()

    except Exception as exc:
        log.error("signal_writer_sync: FAILED for user=%s error=%s", user_id, exc)
        conn.rollback()
