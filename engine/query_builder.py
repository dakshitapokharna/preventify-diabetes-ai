"""
engine/query_builder.py — Phase 2 query construction

Builds the retrieval query that is embedded and sent to pgvector.
The enriched query is NEVER shown to the patient.

Three input paths handled by build_phase2_query():

  Path 1 — New user (no stored profile)
      → current_message + location anchor (default Kerala)
      → "Can I eat rice? [Kerala India]"

  Path 2 — Returning user, fresh question
      → enrich current_message with profile context (diabetes_type, flags, primary med, location)
      → "Can I eat rice? [Patient context: T2DM; ckd; on metformin; Kerala India]"

  Path 3 — Mid-clarification turn (Phase 1 set mid_clarification_resolved=True)
      → merge original patient question from session_turns with current clarification answer
      → then enrich with profile context if available
      → "my feet go numb at night — clarification: burning and tingling in both feet
         [Patient context: T2DM; on metformin; Kerala India]"

  Location default: Kerala India — always present unless profile.location_hint is set to
  something else (e.g. "Mumbai"). session_manager.py ensures location_hint is never empty.

Option C decision (see PHASE1_CONTEXT_ENGINE_SPEC.md Item 3):
  Phase 1 detects mid-clarification turns from conversation context — no separate DB flag.
  The session_turns list already carries the required conversational history.
  The combined query is built here in Path 3.

Usage:
    from engine.query_builder import build_phase2_query

    query = build_phase2_query(
        current_message=validated_english_message,
        session_turns=session_turns,          # last N turns, current NOT included
        profile=stored_user_profile,          # dict or None for new users
        mid_clarification_resolved=phase1_output["mid_clarification_resolved"],
    )
    # query → embed with bge-large-en-v1.5 → pgvector ANN search
"""

from typing import Optional


def build_phase2_query(
    current_message: str,
    session_turns: list,
    profile: Optional[dict],
    mid_clarification_resolved: bool,
) -> str:
    """
    Build the retrieval query for Phase 2 embedding and pgvector ANN search.

    Args:
        current_message:            The patient's current message (English, already validated).
        session_turns:              Last N turns as a list of {"role": str, "content": str}.
                                    Must NOT include the current message — only prior turns.
        profile:                    Stored user profile dict, or None for new users.
                                    Used fields: diabetes_type (str), condition_flags (list),
                                    medications_mentioned (list), location_hint (str).
        mid_clarification_resolved: True when Phase 1 detected the current message is the
                                    patient answering a prior bot clarifying question.

    Returns:
        Enriched query string, ready to embed. Never shown to patient.

    Examples:
        New user (defaults to Kerala):
            build_phase2_query("Can I eat rice?", [], None, False)
            → "Can I eat rice? [Kerala India]"

        Returning user in Kerala, fresh question:
            build_phase2_query("Can I eat rice?", [...], profile, False)
            → "Can I eat rice? [Patient context: T2DM; ckd; on metformin; Kerala India]"

        Returning user in Mumbai:
            build_phase2_query("Can I eat rice?", [...], profile, False)
            → "Can I eat rice? [Patient context: T2DM; ckd; on metformin; Mumbai India]"

        Mid-clarification:
            build_phase2_query("burning and tingling", [...], profile, True)
            → "my feet go numb at night — clarification: burning and tingling
               [Patient context: T2DM; on metformin; Kerala India]"
    """
    # ── Step 1: resolve base query ─────────────────────────────────────────────
    if mid_clarification_resolved:
        base_query = _build_mid_clarification_query(session_turns, current_message)
    else:
        base_query = current_message

    # ── Step 2: resolve location — default Kerala unless profile says otherwise ─
    # session_manager.py always sets location_hint="Kerala" for new/unknown users,
    # so profile location is always set by the time it reaches here.
    location = (profile or {}).get("location_hint") or "Kerala"
    location_tag = f"{location} India"

    # ── Step 3: enrich with profile context ────────────────────────────────────
    if profile:
        return _enrich_with_profile(base_query, profile, location_tag)

    return f"{base_query} [{location_tag}]"


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_mid_clarification_query(session_turns: list, current_answer: str) -> str:
    """
    Merge the original patient question with the current clarification answer.

    With Option C (conversation context detection), session_turns holds the prior
    exchange. The expected pattern when this is called:

        session_turns = [
            ...,
            {"role": "patient", "content": "<original question>"},   ← we want this
            {"role": "bot",     "content": "<clarifying question>"},
        ]
        current_answer = "<patient's clarification answer>"           ← passed in

    We walk backward through session_turns to find the last patient turn —
    that is the original question. We do NOT use the bot turn (the clarifying
    question itself adds noise to the embedding query).

    Edge case: no prior patient turn found (first-ever message, edge case in testing).
    In that case, fall back to current_answer alone — Phase 2 proceeds with imperfect
    context rather than failing.

    Returns:
        "<original question> — clarification: <current answer>"
        or current_answer alone if no prior patient turn is found.
    """
    original_question = None
    for turn in reversed(session_turns):
        if turn.get("role") == "patient":
            original_question = turn["content"]
            break

    if not original_question:
        # No prior patient turn found — should not normally happen with Option C,
        # but must not crash. Proceed with current answer alone.
        return current_answer

    return f"{original_question} — clarification: {current_answer}"


def _enrich_with_profile(query: str, profile: dict, location_tag: str) -> str:
    """
    Append a [Patient context: ...] suffix to the retrieval query.

    This helps bge-large-en-v1.5 surface more relevant chunks. For a patient with
    CKD, "Can I eat rice? [Patient context: T2DM; ckd; on metformin; Kerala India]"
    surfaces KDIGO protein/carbohydrate guidance that a bare query may not rank.

    Rules:
    - location_tag is always included (defaults to "Kerala India")
    - condition_flags: all active flags joined with ", " (all are clinically relevant)
    - medications_mentioned: first item only — enough signal, avoids query bloat

    Args:
        query:        Base query (either current_message or merged mid-clarification query)
        profile:      User profile dict with keys diabetes_type, condition_flags,
                      medications_mentioned. Extra keys are ignored.
        location_tag: e.g. "Kerala India" or "Mumbai India" — always appended.

    Returns:
        Enriched query string with location always present.
    """
    context_parts = []

    diabetes_type = profile.get("diabetes_type") or ""
    if diabetes_type:
        context_parts.append(diabetes_type)

    condition_flags = profile.get("condition_flags") or []
    if condition_flags:
        context_parts.append(", ".join(condition_flags))

    medications = profile.get("medications_mentioned") or []
    if medications:
        # First medication is the most salient — typically the primary OAD or insulin
        context_parts.append(f"on {medications[0]}")

    context_parts.append(location_tag)
    return f"{query} [Patient context: {'; '.join(context_parts)}]"
