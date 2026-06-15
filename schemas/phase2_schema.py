"""
Phase 2 — RAG Pipeline schema: condition flag signals, retrieval filter builder,
constraint checker, chunk formatter, and fallback constants.

This module is the Phase 2 equivalent of schemas/phase1_schema.py.
It contains no LLM calls — pure logic that phase2_runner.py imports.

Contents:
  CKD_SIGNALS, CARDIO_SIGNALS, RAMADAN_SIGNALS, HYPERTENSION_SIGNALS
      → keyword lists used by resolve_condition_flags() to scan the current message.
      → Combined with stored profile flags (which are permanent once set).

  resolve_condition_flags(message, stored_flags)
      → Returns set of active flags for this turn.

  build_retrieval_filter(flags)
      → Returns (retrieval_tier_filter, condition_trigger_filter) for the pgvector WHERE clause.

  CONSTRAINT_PATTERNS
      → Regex patterns that detect safety rule violations in Gemini's generated text.

  check_constraints(text)
      → Scans generated response for violations. Returns (ok: bool, violations: list[str]).

  format_chunks_for_prompt(chunks)
      → Formats top-5 chunk dicts into the <clinical_context> block for the Gemini prompt.

  PHASE2_FALLBACK
      → Safe default response returned when Phase 2 fails for any reason.

  PHASE2_CONSTRAINT_FALLBACK
      → Response returned when the generated text fails the constraint check.
"""

import re
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Condition flag keyword signals
#
# Design rules:
#   - Lowercase only — matched against message.lower()
#   - Short phrases, not full sentences — match substrings
#   - Each list covers both patient lay language AND clinical terms
#   - Stored profile flags are permanent — these only handle NEW signal detection
#     from the current message. See resolve_condition_flags() below.
# ─────────────────────────────────────────────────────────────────────────────

CKD_SIGNALS = [
    # Clinical terms
    "creatinine", "egfr", "gfr", "dialysis", "kidney failure", "renal",
    "nephropathy", "protein in urine", "proteinuria", "albumin in urine",
    # Lay language
    "kidney problem", "kidney issue", "kidney disease", "my kidneys",
    "kidneys are affected", "kidney not working", "urine has protein",
    "doctor said about kidneys", "kidney specialist",
]

CARDIO_SIGNALS = [
    # Clinical terms
    "cardiac", "cardiovascular", "angina", "myocardial",
    "heart failure", "coronary", "angioplasty", "stent",
    # Lay language
    "heart problem", "heart attack", "chest pain", "chest tightness",
    "heart disease", "bypass", "bypass surgery", "heart surgery",
    "my heart", "palpitations", "heart beating fast",
]

RAMADAN_SIGNALS = [
    # Both spellings common in Kerala
    "ramadan", "ramzan", "roza", "rozah",
    # Related concepts
    "iftar", "suhoor", "sehri", "fasting for religion",
    "islamic fasting", "religious fasting", "namaz fasting",
    "roza rakhna",  # Urdu/Hindi — used by some Kerala Muslims
]

HYPERTENSION_SIGNALS = [
    # Clinical terms
    "hypertension", "blood pressure high",
    # Lay language — exact phrases (substring match on lowercased message)
    "bp high", "bp is high", "bp always high",   # covers "my bp is always high"
    "bp problem", "pressure is high",
    "blood pressure tablet", "bp tablet", "pressure medicine",
    "pressure high", "bp going up", "doctor said bp",
    "high bp", "bp issue", "bp too high",
]

# All flags in one place — must match CONDITION_FLAGS in phase1_schema.py
ALL_CONDITION_FLAGS = {"ckd", "cardio", "ramadan", "hypertension"}

# Maps flag → signal list for resolve_condition_flags()
_FLAG_SIGNAL_MAP = {
    "ckd":          CKD_SIGNALS,
    "cardio":       CARDIO_SIGNALS,
    "ramadan":      RAMADAN_SIGNALS,
    "hypertension": HYPERTENSION_SIGNALS,
}


# ─────────────────────────────────────────────────────────────────────────────
# Condition flag resolution
#
# Called BEFORE pgvector search. Combines:
#   (a) keyword scan of current message — catches new mentions
#   (b) stored profile flags — permanent, never cleared
#
# Returns a set of active flag strings. Empty set = Tier 1 only.
# ─────────────────────────────────────────────────────────────────────────────

def resolve_condition_flags(
    message: str,
    stored_flags: Optional[list] = None,
) -> set:
    """
    Return the set of condition flags active for this turn.

    Args:
        message:      Current patient message (English, lowercase accepted).
        stored_flags: condition_flags list from the user's DB profile.
                      None or empty = new user or no flags stored yet.

    Returns:
        set of strings from {"ckd", "cardio", "ramadan", "hypertension"}

    Examples:
        resolve_condition_flags("my creatinine is high", [])
        → {"ckd"}

        resolve_condition_flags("can I eat rice?", ["ckd"])
        → {"ckd"}   ← stored CKD flag keeps KDIGO in retrieval even if not mentioned

        resolve_condition_flags("I have heart problem and doing roza", [])
        → {"cardio", "ramadan"}
    """
    active = set()
    message_lower = message.lower()

    # (a) Scan current message for new signals
    for flag, signals in _FLAG_SIGNAL_MAP.items():
        for signal in signals:
            if signal in message_lower:
                active.add(flag)
                break  # one match is enough per flag

    # (b) Merge stored profile flags — they are permanent
    if stored_flags:
        for flag in stored_flags:
            if flag in ALL_CONDITION_FLAGS:
                active.add(flag)

    return active


# ─────────────────────────────────────────────────────────────────────────────
# Retrieval filter builder
#
# Converts the active flag set into the SQL filter parameters for pgvector.
# Returns (tier_filter, trigger_filter) — used in the ANN search SQL query.
# ─────────────────────────────────────────────────────────────────────────────

def build_retrieval_filter(flags: set) -> tuple:
    """
    Build pgvector WHERE clause parameters from the active condition flags.

    Returns:
        (tier_filter: str | list[str], trigger_filter: list | None)

    SQL usage in phase2_runner.py:
        tier_filter, trigger_filter = build_retrieval_filter(flags)

        if trigger_filter is None:
            # Simple: WHERE retrieval_tier = 'core'
        else:
            # WHERE retrieval_tier = ANY($tier_filter)
            # AND (condition_trigger IS NULL OR condition_trigger = ANY($trigger_filter))

    Examples:
        build_retrieval_filter(set())
        → ("core", None)
        # Tier 1 only — RSSDI, ICMR, ADA, ICMR-NIN, Anoop Misra

        build_retrieval_filter({"ckd"})
        → (["core", "triggered"], ["ckd"])
        # Tier 1 + KDIGO 2022

        build_retrieval_filter({"ckd", "cardio"})
        → (["core", "triggered"], ["ckd", "cardio"])
        # Tier 1 + KDIGO 2022 + ESC 2023 + WHO HEARTS (hypertension flag for WHO)

    Note: condition_trigger IS NULL covers all Tier 1 chunks (which have null condition_trigger).
    Tier 2 chunks have condition_trigger set to their flag: "ckd", "cardio", "ramadan", "hypertension".
    """
    if not flags:
        return ("core", None)
    else:
        return (["core", "triggered"], list(flags))


# ─────────────────────────────────────────────────────────────────────────────
# Constraint checker
#
# Scans Gemini's generated response for safety rule violations.
# Called AFTER generation. If violations are found:
#   - The violation is logged in logs/phase2_failures.jsonl
#   - PHASE2_CONSTRAINT_FALLBACK is returned to the patient instead
#   - The original (violating) text is never shown to the patient
#
# Patterns:
#   (pattern_str, violation_name)
#
# Design notes:
#   - Specific dose pattern (\d+\s*mg) catches "500mg", "500 mg", "1000mg"
#   - Insulin units (\d+\s*unit) catches "10 units", "20 units"
#   - Medication-stop patterns catch direct instructions to stop/reduce
#   - Diagnosis patterns catch definitive statements like "you have neuropathy"
#   - Patterns are intentionally conservative — borderline cases pass through.
#     It is worse to block a valid response than to let a mild phrasing through.
#   - False positive rate should be monitored weekly (phase2_failures.jsonl).
# ─────────────────────────────────────────────────────────────────────────────

CONSTRAINT_PATTERNS = [
    # Rule 1 — specific drug dose
    # NOTE: mg/dL and mg/dl are blood glucose / lab units — NOT drug doses. Excluded via negative lookahead.
    # "200 mg/dL" (blood sugar reading) must NOT trigger this. "500 mg" (drug dose) must.
    (r"\b\d+\s*mg(?!\s*/\s*d[lL])\b",                       "specific_dose_mg"),
    (r"\b\d+\s*unit[s]?\b",                                 "specific_dose_units"),
    (r"\b\d+\s*IU\b",                                       "specific_dose_IU"),
    (r"\b\d+\s*mcg\b",                                      "specific_dose_mcg"),

    # Rule 2 — tell patient to stop or change medication
    (r"stop\s+(your\s+)?(tablet|medicine|medication|insulin|injection)",  "stop_medication"),
    (r"discontinue\s+(your\s+)?(tablet|medicine|medication)",            "stop_medication"),
    (r"reduce\s+(your\s+)?(dose|tablet|medicine|insulin)",               "reduce_dose"),
    (r"take\s+less\s+(tablet|medicine|insulin)",                         "reduce_dose"),
    (r"cut\s+(your\s+)?(dose|tablet)\s+(in\s+half|by\s+half)",          "reduce_dose"),
    (r"you\s+(can|could|should)\s+skip\s+(your\s+)?(dose|tablet)",      "skip_dose"),

    # Rule 3 — diagnosis
    (r"you\s+have\s+(type\s*[12]\s+)?(diabetes|neuropathy|retinopathy|nephropathy|CKD|heart\s+disease)", "diagnosis"),
    (r"you\s+are\s+(diabetic|pre-diabetic|prediabetic)",                 "diagnosis"),
    (r"you\s+are\s+not\s+(diabetic|pre-diabetic)",                       "diagnosis"),
    (r"this\s+(is|sounds\s+like)\s+(definitely|clearly)\s+neuropathy",  "diagnosis"),

    # Rule 4 — lab result as final conclusion
    (r"your\s+(creatinine|egfr|hba1c|fbs|ppbs).+means\s+you\s+have",   "lab_conclusion"),
    (r"your\s+(stage|grade)\s+[1-5]\s+(ckd|kidney\s+disease)",         "lab_conclusion"),
]


def check_constraints(text: str) -> tuple:
    """
    Scan generated response text for safety constraint violations.

    Args:
        text: The full text returned by Gemini 2.5 Pro.

    Returns:
        (ok: bool, violations: list[str])
        ok=True  → no violations found — safe to send to patient
        ok=False → violations found — return PHASE2_CONSTRAINT_FALLBACK instead

    Examples:
        check_constraints("Take 500 mg metformin with breakfast.")
        → (False, ["specific_dose_mg"])

        check_constraints("Your doctor will decide the right dose for you.")
        → (True, [])
    """
    text_lower = text.lower()
    violations = []
    for pattern, name in CONSTRAINT_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            violations.append(name)
    ok = len(violations) == 0
    return ok, violations


# ─────────────────────────────────────────────────────────────────────────────
# Chunk formatter
#
# Converts the top-5 reranked chunks into the <clinical_context> block
# injected into Gemini 2.5 Pro's prompt.
#
# Format design choices:
#   - Numbered [1]–[5] so Gemini can reference chunks internally
#   - Source + section title gives Gemini grounding context
#   - Year shown — Gemini should prefer more recent evidence if conflict
#   - Grade priority shown — 1 (strongest RCT/guideline) → 5 (consensus/contraindicated)
#   - safety_critical chunks get a visible [SAFETY CAUTION] label so Gemini
#     knows not to recommend them as positive interventions
#   - Chunks sorted by grade_priority ascending (best evidence first) before calling this
# ─────────────────────────────────────────────────────────────────────────────

def format_chunks_for_prompt(chunks: list) -> str:
    """
    Format top-5 retrieved chunks into a <clinical_context> block for Gemini 2.5 Pro.

    Args:
        chunks: List of chunk dicts, already sorted by grade_priority ascending.
                Each dict must have: source, year, section_title, text,
                grade_priority, safety_critical.
                (All fields are present in preventify_corpus rows.)

    Returns:
        Multi-line string ready to be injected before the patient's message.

    Example output:
        <clinical_context>
        The following clinical evidence has been retrieved for this patient query.
        Ground your response in this evidence. Do not cite source names to the patient.

        [1] RSSDI_2022 (2022) — Glycemic Targets | Grade Priority: 1
        [RSSDI 2022 — Glycemic Targets]
        HbA1c target < 7.0% for most patients with T2DM...

        [2] ADA_2026 (2026) — Nutrition Therapy | Grade Priority: 2
        [ADA 2026 — Nutrition Therapy]
        South Asian patients typically consume higher carbohydrate diets...

        [3] ESC_2023_CV_DM (2023) — Drug Therapy | Grade Priority: 5 [SAFETY CAUTION — DO NOT RECOMMEND]
        ...
        </clinical_context>
    """
    if not chunks:
        return (
            "<clinical_context>\n"
            "No clinical evidence was retrieved for this query. "
            "Answer only from general DSMES educator knowledge. If uncertain, say so.\n"
            "IMPORTANT: Even without retrieved evidence, all food and diet examples MUST use Kerala "
            "foods (rice in ladles, matta rice, kappa, fish like mathi/ayala, chaaya, puttu, idli, "
            "appam, kadala, curd). Never suggest canned foods, pasta, brown rice, oats, or Western "
            "staples unless the patient's location is not Kerala.\n"
            "</clinical_context>"
        )

    lines = [
        "<clinical_context>",
        "The following clinical evidence has been retrieved for this patient query.",
        "Ground your response in this evidence. Do not cite source names to the patient.",
        "",
    ]

    for i, chunk in enumerate(chunks, start=1):
        source        = chunk.get("source", "Unknown")
        year          = chunk.get("year", "")
        section_title = chunk.get("section_title", "")
        text          = chunk.get("text", "")
        grade         = chunk.get("grade_priority", "?")
        safety_crit   = chunk.get("safety_critical", False)

        header = f"[{i}] {source} ({year}) — {section_title} | Grade Priority: {grade}"
        if safety_crit:
            header += " [SAFETY CAUTION — DO NOT RECOMMEND as positive intervention]"

        lines.append(header)
        lines.append(text.strip())
        lines.append("")

    lines.append("</clinical_context>")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Fallback responses
#
# PHASE2_FALLBACK — returned when Phase 2 pipeline fails (API error, timeout, etc.)
# PHASE2_CONSTRAINT_FALLBACK — returned when constraint check fails on generated text
#
# Both are safe to send to the patient. Neither contains clinical claims.
# ─────────────────────────────────────────────────────────────────────────────

PHASE2_FALLBACK_TEXT = (
    "I'm sorry — I'm having a little trouble finding the right information for you right now. "
    "Please try again in a moment. "
    "If your question is urgent, please contact your nearest government health centre or clinic."
)

PHASE2_CONSTRAINT_FALLBACK_TEXT = (
    "That is a good question — let me give you what I can as a diabetes educator. "
    "For the specific details about your medicines or test results, "
    "your doctor is the right person to advise you. "
    "Please bring this question to your next clinic visit, or call the clinic if it is urgent."
)

PHASE2_FALLBACK = {
    "text":                   PHASE2_FALLBACK_TEXT,
    "chunks_used":            [],
    "chunks_detail":          [],   # [{source, section, grade_priority}] — for debug panel + audit
    "condition_flags_active": [],
    "query_cache_hit":        False,
    "reranker_scores":        [],
    "constraint_violation":   False,
    "constraint_violations":  [],   # list of violation name strings from check_constraints()
    "_fallback":              True,
    "_fallback_reason":       "",   # filled by run_phase2() with error type
}
