"""
Phase 1 — Context Engine output schema.

Used in two places:
  1. Passed to Gemini 2.0 Flash as response_schema (guarantees syntactically valid JSON)
  2. Used by validate_phase1_output() to fill defaults for any missing fields

propertyOrdering is required for Gemini 2.0 Flash — without it, properties come out
alphabetically. Gemini 2.5+ does not need this, but including it is harmless.

Usage:
    from schemas.phase1_schema import PHASE1_RESPONSE_SCHEMA, PHASE1_FALLBACK
    from google.genai import types

    response = await client.aio.models.generate_content(
        model="gemini-2.0-flash-001",
        contents=[...],
        config=types.GenerateContentConfig(
            system_instruction=PHASE1_SYSTEM_PROMPT,   # loaded from prompts/phase1_system_prompt.txt
            response_mime_type="application/json",
            response_schema=PHASE1_RESPONSE_SCHEMA,
        ),
    )
"""

# ---------------------------------------------------------------------------
# Intent values — ADCES7-mapped
# ---------------------------------------------------------------------------
INTENT_ENUM = [
    "healthy_eating",       # food, diet, carb content, GI, Kerala foods, portions, chaaya, festivals
    "being_active",         # exercise, walking, monsoon alternatives, foot safety during activity
    "taking_medication",    # mechanism, side effects, storage, injection technique, missed dose
                            # NOTE: dose-change questions → escalation_only
    "monitoring",           # SMBG, HbA1c meaning, how often to test, target ranges
    "problem_solving",      # hypoglycaemia response, sick day, travel, Ramadan/Ekadashi/Navratri fasting
    "reducing_risks",       # foot care, eye exam schedule, kidney signs, smoking, wound recognition
    "healthy_coping",       # fear of insulin, emotional distress, family as unit, caregiver questions
    "escalation_only",      # dose change, diagnosis request, lab interpretation needing RMP, Tier 3/4
    "general_dsmes",        # catch-all — doesn't fit above categories
]

MEDICATION_VOCABULARY = [
    "oral_antidiabetic_unspecified",  # "white tablet", "tablet in morning", name unknown
    "metformin",
    "sulfonylurea",                   # glipizide, glimepiride, glyburide
    "dpp4_inhibitor",                 # sitagliptin, vildagliptin, alogliptin
    "sglt2_inhibitor",                # dapagliflozin, empagliflozin, canagliflozin
    "glp1_ra",                        # semaglutide, liraglutide
    "insulin_basal",                  # "long-acting injection", "night injection"
    "insulin_premixed",               # "30/70", "mixed insulin"
    "insulin_unspecified",            # "I take injection", type unknown
    "bp_tablet_unspecified",          # antihypertensive, name unknown
    "statin_unspecified",             # "cholesterol tablet"
]

COMPLICATION_VOCABULARY = [
    "neuropathy_suspected",           # feet burn / tingle / numb / burning in legs
    "retinopathy_suspected",          # blurry vision, vision changes, floaters
    "nephropathy_suspected",          # protein in urine, "kidney issue" (before ckd flag)
    "foot_wound_present",             # wound, sore, ulcer on foot — also Tier 3 risk trigger
    "autonomic_suspected",            # dizzy when standing, gut problems, night sweats
    "erectile_dysfunction_mentioned", # SENSITIVE — stored but NEVER surfaced in patient responses
                                      # used for clinical profile completeness and lead scoring only
]

CONDITION_FLAGS = ["ckd", "cardio", "ramadan", "hypertension"]

# ---------------------------------------------------------------------------
# Gemini response_schema — passed to generate_content / generate_content_async
# propertyOrdering is required for Gemini 2.0 Flash.
# ---------------------------------------------------------------------------
PHASE1_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": INTENT_ENUM,
            "description": (
                "Which ADCES7 self-care behavior this question falls under. "
                "Use escalation_only for dose-change requests, new diagnoses, "
                "Tier 3/4 emergency signals. Use general_dsmes as catch-all."
            ),
        },
        "qds_score": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5,
            "description": (
                "Question Depth Score: 1=general awareness, 2=personal relevance, "
                "3=active management, 4=complication concern, 5=distressed/complex."
            ),
        },
        "context_sufficient": {
            "type": "boolean",
            "description": (
                "True if enough context exists to retrieve a clinical answer now. "
                "False if a clarifying question is needed first. "
                "Always True for QDS 1, 2, and 5. Always True for escalation_only."
            ),
        },
        "clarifying_questions": {
            "type": "array",
            "description": "Empty array when context_sufficient is true. Max 2 items.",
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The question text shown to the patient.",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["buttons", "list", "open"],
                        "description": (
                            "buttons: 2–3 preset choices. "
                            "list: 4–10 options. "
                            "open: free text, no options (use for descriptions, durations, distress)."
                        ),
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Empty array when format is open. 2–3 items for buttons. Up to 10 for list.",
                    },
                },
                "required": ["text", "format", "options"],
                "propertyOrdering": ["text", "format", "options"],
            },
        },
        "profile_signals": {
            "type": "object",
            "description": "Extracted from every message. Written to patient profile regardless of context_sufficient.",
            "properties": {
                "diabetes_type": {
                    "type": "string",
                    "enum": ["T1DM", "T2DM", "GDM", "prediabetes", "suspected", ""],
                    "description": "Empty string if not mentioned. suspected = patient says sugar is high but no formal diagnosis stated.",
                },
                "medications_mentioned": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": f"Use controlled vocabulary: {MEDICATION_VOCABULARY}. Empty array if none mentioned.",
                },
                "insulin_user": {
                    "type": "boolean",
                    "description": (
                        "True if patient mentions injection or insulin in this message. "
                        "False if not mentioned or patient explicitly says they do not use insulin. "
                        "Default False — the DB merge rule ensures it is never reset to False "
                        "once True has been stored."
                    ),
                },
                "condition_flags": {
                    "type": "array",
                    "items": {"type": "string", "enum": CONDITION_FLAGS},
                    "description": "Conditions that trigger Tier 2 sources. Permanent once set. ckd / cardio / ramadan / hypertension.",
                },
                "complications_mentioned": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": f"Use controlled vocabulary: {COMPLICATION_VOCABULARY}. Empty array if none.",
                },
                "location_hint": {
                    "type": "string",
                    "description": "City or district in Kerala if mentioned. Empty string if not mentioned.",
                },
                "session_context": {
                    "type": "string",
                    "enum": ["self", "family_member_inquiry"],
                    "description": "self = patient asking about themselves. family_member_inquiry = asking about a relative.",
                },
            },
            "required": [
                "diabetes_type", "medications_mentioned", "insulin_user",
                "condition_flags", "complications_mentioned", "location_hint", "session_context",
            ],
            "propertyOrdering": [
                "diabetes_type", "medications_mentioned", "insulin_user",
                "condition_flags", "complications_mentioned", "location_hint", "session_context",
            ],
        },
        "mid_clarification_resolved": {
            "type": "boolean",
            "description": (
                "True if this message is the patient answering a clarifying question the bot "
                "asked in the previous turn. When true, context_sufficient must also be true. "
                "False for fresh questions or when patient pivoted to a new topic."
            ),
        },
    },
    "required": [
        "intent", "qds_score", "context_sufficient", "clarifying_questions",
        "profile_signals", "mid_clarification_resolved",
    ],
    "propertyOrdering": [
        "intent", "qds_score", "context_sufficient", "clarifying_questions",
        "profile_signals", "mid_clarification_resolved",
    ],
}

# ---------------------------------------------------------------------------
# Fallback — used when Phase 1 API call fails or returns invalid/partial JSON
# _fallback: True marks this as a fallback — never shown to patient, used for logging
# ---------------------------------------------------------------------------
PHASE1_FALLBACK = {
    "intent": "general_dsmes",
    "qds_score": 2,
    "context_sufficient": True,       # always proceed to Phase 2 on failure — never block patient
    "clarifying_questions": [],
    "profile_signals": {
        "diabetes_type": "",
        "medications_mentioned": [],
        "insulin_user": False,
        "condition_flags": [],
        "complications_mentioned": [],
        "location_hint": "",
        "session_context": "self",
    },
    "mid_clarification_resolved": False,
    "_fallback": True,
    "_fallback_reason": "",           # filled by run_phase1() with error type
}


def validate_phase1_output(raw: dict) -> dict:
    """
    Fill defaults for any missing or invalid fields in a Phase 1 response.
    Structured output (response_mime_type=application/json) guarantees syntax,
    but not semantics — this catches out-of-range values and missing keys.

    Returns a valid Phase 1 output dict. Never raises — always returns something usable.

    IMPORTANT: uses copy.deepcopy to avoid mutating PHASE1_FALLBACK across calls.
    """
    import copy

    # Deep copy so nested profile_signals dict is never shared with PHASE1_FALLBACK
    result = copy.deepcopy(PHASE1_FALLBACK)

    # Mark as NOT a fallback — this is a real (validated) response
    result["_fallback"] = False
    result["_fallback_reason"] = ""

    # intent
    if raw.get("intent") in INTENT_ENUM:
        result["intent"] = raw["intent"]

    # qds_score
    qds = raw.get("qds_score")
    if isinstance(qds, int) and 1 <= qds <= 5:
        result["qds_score"] = qds

    # context_sufficient
    if isinstance(raw.get("context_sufficient"), bool):
        result["context_sufficient"] = raw["context_sufficient"]

    # QDS 1, 2, 5 override — always sufficient (general awareness and personal relevance
    # questions never need clarification; distressed patients must never be asked to wait)
    if result["qds_score"] in (1, 2, 5):
        result["context_sufficient"] = True

    # escalation_only override — always sufficient
    if result["intent"] == "escalation_only":
        result["context_sufficient"] = True

    # clarifying_questions — only valid when context_sufficient is False
    if not result["context_sufficient"]:
        qs = raw.get("clarifying_questions", [])
        if isinstance(qs, list):
            valid_qs = []
            for q in qs[:2]:  # max 2
                if isinstance(q, dict) and "text" in q and "format" in q:
                    fmt = q.get("format", "open")
                    if fmt not in ("buttons", "list", "open"):
                        fmt = "open"
                    opts = q.get("options", [])
                    if not isinstance(opts, list):
                        opts = []
                    if fmt == "open":
                        opts = []
                    elif fmt == "buttons":
                        opts = opts[:3]  # WhatsApp hard limit: 3 buttons
                    elif fmt == "list":
                        opts = opts[:10]
                    valid_qs.append({"text": q["text"], "format": fmt, "options": opts})
            result["clarifying_questions"] = valid_qs
    else:
        result["clarifying_questions"] = []

    # profile_signals — deep copy then merge valid fields from raw
    raw_signals = raw.get("profile_signals", {})
    if isinstance(raw_signals, dict):
        signals = copy.deepcopy(result["profile_signals"])  # deep copy to avoid shared state

        if raw_signals.get("diabetes_type") in ["T1DM", "T2DM", "GDM", "prediabetes", "suspected", ""]:
            signals["diabetes_type"] = raw_signals["diabetes_type"]

        if isinstance(raw_signals.get("medications_mentioned"), list):
            signals["medications_mentioned"] = [
                m for m in raw_signals["medications_mentioned"]
                if isinstance(m, str) and m in MEDICATION_VOCABULARY
            ]

        if isinstance(raw_signals.get("insulin_user"), bool):
            signals["insulin_user"] = raw_signals["insulin_user"]

        if isinstance(raw_signals.get("condition_flags"), list):
            signals["condition_flags"] = [
                f for f in raw_signals["condition_flags"] if f in CONDITION_FLAGS
            ]

        if isinstance(raw_signals.get("complications_mentioned"), list):
            signals["complications_mentioned"] = [
                c for c in raw_signals["complications_mentioned"]
                if isinstance(c, str) and c in COMPLICATION_VOCABULARY
            ]

        if isinstance(raw_signals.get("location_hint"), str):
            signals["location_hint"] = raw_signals["location_hint"]

        if raw_signals.get("session_context") in ("self", "family_member_inquiry"):
            signals["session_context"] = raw_signals["session_context"]

        result["profile_signals"] = signals

    # mid_clarification_resolved
    if isinstance(raw.get("mid_clarification_resolved"), bool):
        result["mid_clarification_resolved"] = raw["mid_clarification_resolved"]

    # if mid_clarification_resolved, context must be sufficient
    if result["mid_clarification_resolved"]:
        result["context_sufficient"] = True
        result["clarifying_questions"] = []

    return result
