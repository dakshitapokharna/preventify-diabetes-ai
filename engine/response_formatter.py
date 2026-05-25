"""
engine/response_formatter.py — Risk tier nudge merge into patient response

Merges Phase 1 (context classification) output + Risk Engine tier assignment
into the final patient-facing response structure.

Pipeline position:
    Phase 1 runs → Risk Engine runs (parallel) → build_response() merges both

Tier 4 bypass:
    The Risk Engine handles Tier 4 emergencies directly — it does NOT go through
    this module. If risk_tier == 4, build_response() returns an emergency stub
    and the Risk Engine replaces it with the actual emergency response.

Tier 3 subtype:
    Tier 3 has three variants requiring different action sequences:
      "foot_wound"   → Tier 3A — wound/sore not healing
      "high_bg"      → Tier 3B — persistently very high BG (>300 for multiple days)
      "hypoglycemia" → Tier 3C — active low BG / severe dizziness
    The Risk Engine must pass tier_3_subtype alongside risk_tier when tier == 3.
    If subtype is missing or unknown, the "default" fallback text is used.

Nudge placement:
    Tier 1, 2  → nudge appended AFTER clarifying question or Phase 2 response
    Tier 3     → nudge prepended BEFORE clarifying question (urgency first);
                 Tier 3C (hypoglycemia) always goes first regardless of context
    Tier 4     → not handled here

⚠️  CLINICAL REVIEW STATUS:
    All nudge text below is evidence-grounded (see research citations in
    PHASE1_CONTEXT_ENGINE_SPEC.md Item 5 and BOT_CONVERSATION_ARCHITECTURE.md Section 11)
    but has NOT yet received Dr. Rakesh clinical sign-off.
    Items marked [DR. RAKESH REVIEW] must be approved before production deployment.
    Items marked [ENGINEERING LOCKED] are safe to ship without clinical review.

Sources:
    - Voices of Care Kerala T2DM study (Frontiers Public Health 2024, PMC)
    - Diabetic Foot Talk-Time Framework (PMC 2025)
    - ADA 2026 Standards S6 — Glycemic Goals, Hypoglycemia, Hyperglycemic Crises
    - Tailored vs Generic Messaging for Disadvantaged Patients (BMC 2015)
    - Financial Burden of Diabetes in India (PMC 2024)
    - South Asia Diabetes Families (PMC 2025)
"""

from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Nudge text — evidence-grounded drafts
# ─────────────────────────────────────────────────────────────────────────────
#
# Design principles applied (from Kerala patient barriers research):
#   - Positive-reason framing over threat framing (avoids fatalism trigger)
#   - "PHC/FHC" or "nearest clinic" over "hospital" (removes cost anxiety signal)
#   - Plain language — max 2–3 short sentences, active voice, no medical jargon
#   - Family framing in Tier 3 (transport dependency is a documented Kerala barrier)
#   - Soft deadline in Tier 1 to prevent indefinite deferral without causing urgency
#   - Tier 3C: self-action first, then care-seeking (ADA 15-15 rule, plain language)
#
# ⚠️  ALL TIER TEXT BELOW NEEDS Dr. Rakesh clinical sign-off before production.
# ─────────────────────────────────────────────────────────────────────────────

TIER_NUDGE_TEXT: dict = {

    # Tier 0 — Education only. No nudge.                  [ENGINEERING LOCKED]
    0: None,

    # Tier 1 — Low concern. Nudge to next scheduled visit. [DR. RAKESH REVIEW]
    # Soft deadline ("next month or so") prevents indefinite deferral without urgency.
    # "Worth mentioning" is intentionally low-stakes — matches Tier 1 clinical weight.
    # Open question for Dr. Rakesh: is "one month" the right soft window for Tier 1?
    1: (
        "This is worth mentioning to your doctor at your next visit "
        "— within the next month or so."
    ),

    # Tier 2 — Moderate concern. Clinic within 1–2 weeks.  [DR. RAKESH REVIEW]
    # Positive reason ("catching it early makes treatment easier") used instead of
    # threat framing — Kerala research shows fatalism increases with threat messages.
    # "PHC/FHC" signals accessible, lower-cost care; avoids "hospital" cost association.
    # Open question for Dr. Rakesh: is "PHC/FHC" the right destination to name here,
    # or should Sugar Care Clinics be referenced?
    2: (
        "It is good to check this with a doctor in the next week or two — "
        "catching it early makes treatment easier. "
        "You do not need to wait for your next scheduled visit. "
        "Your nearest government health centre (PHC or FHC) can help."
    ),

    # Tier 3 — High concern. Three variants for different action sequences.
    3: {

        # Tier 3A — Foot wound / non-healing sore          [DR. RAKESH REVIEW]
        # "Even a small wound can become serious" explains WHY — knowledge deficit
        # is the primary predictor of poor foot care behavior in India (only 22% of
        # Indian diabetic patients had ever received foot care advice from a provider).
        # Family framing addresses the documented transport/companionship barrier.
        # Three sentences — slightly longer than ideal but clinically necessary here.
        # Open question for Dr. Rakesh: confirm "even a small wound can become serious"
        # is the right threshold statement, and whether "biscuit" is a safe fast-carb
        # example for Kerala patients (some biscuits are too slow-acting).
        "foot_wound": (
            "A wound or sore on the foot that is not healing needs to be seen "
            "by a doctor within the next day or two. "
            "For people with diabetes, even a small wound can become serious "
            "if not checked early. "
            "Please visit a clinic soon — take a family member with you if you can."
        ),

        # Tier 3B — Persistently very high BG (>300 for multiple days) [DR. RAKESH REVIEW]
        # Baseline: clinic in 24–48h.
        # Embeds within-message Tier 4 escalation for DKA warning signs — avoids
        # needing a separate conversation turn if patient reports vomiting/confusion.
        # DKA symptom list (vomiting, very thirsty, very weak) from ADA 2026 S6 —
        # open question for Dr. Rakesh: confirm these three are the right patient-language
        # equivalents and whether "very weak" adequately captures altered consciousness.
        "high_bg": (
            "Blood sugar that stays this high for several days needs to be checked "
            "by a doctor within the next day or two. "
            "If you also feel like vomiting, are very thirsty, or feel very weak "
            "— go immediately, do not wait."
        ),

        # Tier 3C — Active low BG / severe dizziness (hypoglycemia) [DR. RAKESH REVIEW]
        # STRUCTURALLY DIFFERENT from 3A and 3B.
        # Self-action MUST come first — ADA 2026 15-15 rule simplified to plain language.
        # Telling an actively hypoglycemic patient to "go to a clinic" without the
        # immediate self-treatment instruction is unsafe.
        # Instruction is in time order: eat → rest → recheck → go if not better.
        # Open question for Dr. Rakesh: confirm "spoonful of sugar, sweet drink, or biscuit"
        # are appropriate Kerala equivalents for 15g fast-acting carbohydrate. Some biscuits
        # are too slow-acting — Dr. Rakesh should replace with better local examples.
        # Note: Tier 3C only fires when patient is currently symptomatic (active BG <70 or
        # active severe dizziness). Historical lows should be Tier 2, not Tier 3C.
        "hypoglycemia": (
            "If your sugar is low right now — eat something sweet immediately. "
            "A spoonful of sugar, a sweet drink, or glucose tablets. "
            "Then rest and check again in 15 minutes. "
            "If you still feel very weak or dizzy after that, "
            "call someone to take you to a clinic right away."
        ),

        # Tier 3 default — fallback if subtype is missing or unrecognised [DR. RAKESH REVIEW]
        # Used when Risk Engine passes tier=3 but no valid subtype.
        # Generic but safe — directs to clinic within 24–48h with family framing.
        "default": (
            "This needs to be checked by a doctor within the next day or two. "
            "Please visit your nearest clinic — take a family member with you if you can."
        ),
    },

    # Tier 4 — Emergency. Handled entirely by Risk Engine. Never reaches here.
    # build_response() returns EMERGENCY_STUB when risk_tier == 4; the Risk Engine
    # replaces this with the actual emergency response (patient safety instructions
    # + RMP notification). The actual Tier 4 response text is pending B4 (RMP loop
    # design) and Dr. Rakesh clinical sign-off.                  [NOT STARTED — B4 blocker]
    4: None,
}


# Sentinel returned when risk_tier == 4 — Risk Engine replaces this immediately.
EMERGENCY_STUB = {
    "text": "__TIER4_EMERGENCY__",
    "risk_tier": 4,
    "_emergency": True,
}


# ─────────────────────────────────────────────────────────────────────────────
# Core merge functions
# ─────────────────────────────────────────────────────────────────────────────

def get_nudge_text(risk_tier: int, tier_3_subtype: Optional[str] = None) -> Optional[str]:
    """
    Resolve the nudge text for a given risk tier and optional subtype.

    Args:
        risk_tier:       0–4 as assigned by the Risk Engine.
        tier_3_subtype:  Required when risk_tier == 3.
                         One of: "foot_wound", "high_bg", "hypoglycemia".
                         Falls back to "default" if missing or unrecognised.

    Returns:
        Nudge text string, or None if no nudge is needed (Tier 0 or Tier 4).
    """
    entry = TIER_NUDGE_TEXT.get(risk_tier)

    if entry is None:
        return None

    if isinstance(entry, dict):
        # Tier 3 — resolve subtype
        subtype = tier_3_subtype or "default"
        return entry.get(subtype, entry["default"])

    return entry


def add_risk_nudge(response_text: str, risk_tier: int,
                   tier_3_subtype: Optional[str] = None) -> str:
    """
    Append or prepend a risk nudge to a Phase 2 response or clarifying question.

    Placement rules:
      Tier 1, 2  → nudge appended after response (two newlines separator)
      Tier 3     → nudge prepended before response (urgency delivered first)
      Tier 0/4   → response returned unchanged

    Args:
        response_text:   The Phase 2 response text or clarifying question text.
        risk_tier:       0–4 as assigned by the Risk Engine.
        tier_3_subtype:  One of "foot_wound", "high_bg", "hypoglycemia" for tier 3.

    Returns:
        Combined string with nudge correctly placed.
    """
    nudge = get_nudge_text(risk_tier, tier_3_subtype)

    if not nudge:
        return response_text

    if risk_tier in (1, 2):
        # Nudge after response — low urgency, let the answer land first
        return f"{response_text}\n\n{nudge}"

    if risk_tier == 3:
        # Nudge before response — urgency must be communicated immediately
        return f"{nudge}\n\nAlso, to help you further — {response_text}"

    # Tier 0 or Tier 4 — should not reach here, but return unchanged to be safe
    return response_text


def build_response(
    phase1_output: dict,
    risk_tier: int,
    tier_3_subtype: Optional[str] = None,
    phase2_text: Optional[str] = None,
) -> dict:
    """
    Merge Phase 1 + Phase 2 outputs + risk nudge into the final patient-facing response.

    Called by the Phase 1 orchestrator (engine/phase1.py) AFTER run_phase2() has
    already been called. The Phase 2 text is passed in via phase2_text.

    Args:
        phase1_output:    Validated Phase 1 output dict (from validate_phase1_output()).
        risk_tier:        0–4 from the Risk Engine (0 until Risk Engine is built).
        tier_3_subtype:   "foot_wound" | "high_bg" | "hypoglycemia" — required if tier == 3.
        phase2_text:      Patient-facing English text from run_phase2(). None when Phase 2
                          was skipped (context_sufficient=False) or on Phase 2 failure.

    Returns:
        dict with keys:
            "text"                  — patient-facing response text (with nudge merged)
            "risk_tier"             — tier number (for frontend debug display)
            "intent"                — intent value from Phase 1 (for analytics)
            "qds_score"             — QDS score from Phase 1 (for lead scoring)
            "_clarifying"           — True if this is a clarifying question turn
            "_clarifying_questions" — present only when _clarifying is True
    """
    # ── Tier 4: bypass everything ──────────────────────────────────────────────
    if risk_tier == 4:
        # Return stub — Risk Engine replaces with actual emergency response
        return EMERGENCY_STUB

    # ── Context sufficient → use Phase 2 text ─────────────────────────────────
    if phase1_output.get("context_sufficient", True):
        # phase2_text is None when Phase 2 was skipped (escalation_only) or failed.
        # Use a safe fallback so the patient always gets a response.
        if not phase2_text:
            phase2_text = (
                "I'm here to help with your diabetes questions. "
                "Could you please rephrase or give me a moment to look that up?"
            )

        final_text = add_risk_nudge(phase2_text, risk_tier, tier_3_subtype)

        return {
            "text": final_text,
            "risk_tier": risk_tier,
            "intent": phase1_output.get("intent", "general_dsmes"),
            "qds_score": phase1_output.get("qds_score", 1),
            "_clarifying": False,
        }

    # ── Context not sufficient → clarifying question + nudge ──────────────────
    clarifying_questions = phase1_output.get("clarifying_questions", [])
    if not clarifying_questions:
        # Fallback: context_sufficient is False but no questions generated.
        # Should not happen after validate_phase1_output() — but handle gracefully.
        clarifying_text = "Could you tell me a little more about your situation?"
    else:
        # Format the first clarifying question as the patient-facing message.
        # The options (buttons/list) are passed separately for the frontend to render.
        q = clarifying_questions[0]
        clarifying_text = q.get("text", "Could you tell me a little more?")

    final_text = add_risk_nudge(clarifying_text, risk_tier, tier_3_subtype)

    return {
        "text": final_text,
        "risk_tier": risk_tier,
        "intent": phase1_output.get("intent", "general_dsmes"),
        "qds_score": phase1_output.get("qds_score", 1),
        "_clarifying": True,
        "_clarifying_questions": clarifying_questions,  # Full list for frontend rendering
    }
