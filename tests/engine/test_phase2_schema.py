"""
tests/engine/test_phase2_schema.py — Unit tests for Phase 2 schema functions

38 tests covering:
  - resolve_condition_flags()    12 tests
  - build_retrieval_filter()      6 tests
  - check_constraints()          14 tests
  - format_chunks_for_prompt()    6 tests

All functions are pure (no DB, no API calls) — tests run without any infrastructure.

Run:
    python -m pytest tests/engine/test_phase2_schema.py -v
"""

import pytest
from schemas.phase2_schema import (
    resolve_condition_flags,
    build_retrieval_filter,
    check_constraints,
    format_chunks_for_prompt,
    PHASE2_FALLBACK,
    PHASE2_FALLBACK_TEXT,
    PHASE2_CONSTRAINT_FALLBACK_TEXT,
)


# ─────────────────────────────────────────────────────────────────────────────
# resolve_condition_flags — 12 tests
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveConditionFlags:

    def test_new_user_no_signals(self):
        """New user, neutral message → empty set."""
        flags = resolve_condition_flags("can I eat rice?", stored_flags=None)
        assert flags == set()

    def test_ckd_keyword_creatinine(self):
        """CKD signal from clinical term."""
        flags = resolve_condition_flags("my creatinine is high")
        assert "ckd" in flags
        assert len(flags) == 1

    def test_ckd_keyword_lay_language(self):
        """CKD signal from lay language."""
        flags = resolve_condition_flags("doctor said I have a kidney problem")
        assert "ckd" in flags

    def test_cardio_keyword_heart_attack(self):
        """Cardio flag from lay language."""
        flags = resolve_condition_flags("I had a heart attack last year")
        assert "cardio" in flags
        assert "ckd" not in flags

    def test_ramadan_keyword_roza(self):
        """Ramadan flag from Urdu term common in Kerala."""
        flags = resolve_condition_flags("I want to keep roza this month")
        assert "ramadan" in flags

    def test_ramadan_keyword_ramzan(self):
        """Ramadan flag from alternate spelling."""
        flags = resolve_condition_flags("during ramzan my sugar goes low")
        assert "ramadan" in flags

    def test_hypertension_keyword_bp(self):
        """Hypertension flag from lay language — signal 'bp is high' is a substring match."""
        flags = resolve_condition_flags("my bp is high")
        assert "hypertension" in flags

    def test_multiple_flags_from_one_message(self):
        """Two flags detected from a single message."""
        flags = resolve_condition_flags("I have heart problem and kidney issue")
        assert "cardio" in flags
        assert "ckd" in flags
        assert len(flags) == 2

    def test_stored_ckd_flag_persists_on_neutral_message(self):
        """Stored CKD flag keeps KDIGO active even with no new mention."""
        flags = resolve_condition_flags("can I eat more protein?", stored_flags=["ckd"])
        assert "ckd" in flags

    def test_stored_and_new_message_flag_are_unioned(self):
        """Stored CKD + cardio in message → both active."""
        flags = resolve_condition_flags("I have chest pain sometimes", stored_flags=["ckd"])
        assert "ckd" in flags
        assert "cardio" in flags

    def test_case_insensitive_signal_detection(self):
        """Signals should match regardless of case in patient message."""
        flags = resolve_condition_flags("BP HIGH AND KIDNEY DISEASE")
        assert "hypertension" in flags
        assert "ckd" in flags

    def test_unknown_stored_flag_is_ignored(self):
        """Invalid flag strings in stored profile should not cause errors."""
        flags = resolve_condition_flags("how to manage sugar?", stored_flags=["unknown_flag", "ckd"])
        assert "ckd" in flags
        assert "unknown_flag" not in flags


# ─────────────────────────────────────────────────────────────────────────────
# build_retrieval_filter — 6 tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildRetrievalFilter:

    def test_empty_flags_returns_core_only(self):
        """No condition flags → Tier 1 only retrieval."""
        tier_filter, trigger_filter = build_retrieval_filter(set())
        assert tier_filter == "core"
        assert trigger_filter is None

    def test_single_flag_returns_both_tiers(self):
        """One condition flag → Tier 1 + Tier 2 retrieval."""
        tier_filter, trigger_filter = build_retrieval_filter({"ckd"})
        assert isinstance(tier_filter, list)
        assert "core" in tier_filter
        assert "triggered" in tier_filter
        assert trigger_filter == ["ckd"]

    def test_two_flags_returns_both_in_trigger_filter(self):
        """Two flags → both in trigger_filter list."""
        tier_filter, trigger_filter = build_retrieval_filter({"ckd", "cardio"})
        assert set(trigger_filter) == {"ckd", "cardio"}

    def test_all_four_flags(self):
        """All four flags → all four in trigger_filter."""
        flags = {"ckd", "cardio", "ramadan", "hypertension"}
        tier_filter, trigger_filter = build_retrieval_filter(flags)
        assert set(trigger_filter) == flags

    def test_trigger_filter_is_list_type(self):
        """trigger_filter must be a list (not set) for asyncpg array binding."""
        _, trigger_filter = build_retrieval_filter({"ckd"})
        assert isinstance(trigger_filter, list)

    def test_tier_filter_is_list_when_flags_present(self):
        """tier_filter must be a list (not string) when condition flags are active."""
        tier_filter, _ = build_retrieval_filter({"cardio"})
        assert isinstance(tier_filter, list)


# ─────────────────────────────────────────────────────────────────────────────
# check_constraints — 14 tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckConstraints:

    def test_clean_response_no_violations(self):
        """Standard educator response with no violations."""
        text = (
            "Metformin is usually taken with food to reduce stomach discomfort. "
            "Your doctor will tell you the right dose and timing for you. "
            "It is one of the most commonly prescribed tablets for Type 2 diabetes."
        )
        ok, violations = check_constraints(text)
        assert ok is True
        assert violations == []

    def test_specific_dose_mg_violation(self):
        """Dose number in mg is a hard violation."""
        ok, violations = check_constraints("Take 500 mg of metformin twice daily.")
        assert ok is False
        assert "specific_dose_mg" in violations

    def test_specific_dose_units_violation(self):
        """Insulin units are a hard violation."""
        ok, violations = check_constraints("You should take 10 units of insulin before dinner.")
        assert ok is False
        assert "specific_dose_units" in violations

    def test_specific_dose_IU_violation(self):
        """IU is a unit used for certain injectable medications."""
        ok, violations = check_constraints("Take 20 IU of basal insulin at bedtime.")
        assert ok is False
        assert "specific_dose_IU" in violations

    def test_stop_medication_violation(self):
        """Telling patient to stop their tablet is a hard violation."""
        ok, violations = check_constraints("You can stop your tablet if your sugar is normal now.")
        assert ok is False
        assert "stop_medication" in violations

    def test_discontinue_medication_violation(self):
        """'Discontinue' is equivalent to 'stop'."""
        ok, violations = check_constraints("Discontinue your medication for a week and see.")
        assert ok is False
        assert "stop_medication" in violations

    def test_reduce_dose_violation(self):
        """Reducing dose instruction is a violation."""
        ok, violations = check_constraints("Try to reduce your dose by half.")
        assert ok is False
        assert "reduce_dose" in violations

    def test_take_less_insulin_violation(self):
        """Telling patient to take less insulin is a violation."""
        ok, violations = check_constraints("Take less insulin if you feel better.")
        assert ok is False
        assert "reduce_dose" in violations

    def test_cut_dose_in_half_violation(self):
        """Dose halving instruction is a violation."""
        ok, violations = check_constraints("Cut your dose in half for a few days.")
        assert ok is False
        assert "reduce_dose" in violations

    def test_skip_dose_violation(self):
        """Advising to skip a dose is a violation."""
        ok, violations = check_constraints("You can skip your dose if you eat very little today.")
        assert ok is False
        assert "skip_dose" in violations

    def test_diagnosis_you_have_diabetes(self):
        """Making a diabetes diagnosis is a hard violation."""
        ok, violations = check_constraints("Based on what you said, you have Type 2 diabetes.")
        assert ok is False
        assert "diagnosis" in violations

    def test_diagnosis_you_are_diabetic(self):
        """'You are diabetic' is a diagnosis statement."""
        ok, violations = check_constraints("Looking at your sugar levels, you are diabetic.")
        assert ok is False
        assert "diagnosis" in violations

    def test_lab_conclusion_violation(self):
        """Interpreting creatinine as a definitive CKD conclusion is a violation."""
        ok, violations = check_constraints("Your creatinine means you have stage 3 CKD.")
        assert ok is False
        assert "lab_conclusion" in violations

    def test_multiple_violations_all_listed(self):
        """Multiple violations in one text should all be reported."""
        text = "You have diabetes. Take 500 mg metformin and stop your other tablet."
        ok, violations = check_constraints(text)
        assert ok is False
        assert len(violations) >= 2
        assert "diagnosis" in violations
        assert "specific_dose_mg" in violations


# ─────────────────────────────────────────────────────────────────────────────
# format_chunks_for_prompt — 6 tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatChunksForPrompt:

    def _make_chunk(self, idx=1, source="RSSDI_2022", year=2022,
                    section_title="Glycemic Targets", grade_priority=1,
                    safety_critical=False, text="HbA1c target < 7.0%."):
        return {
            "chunk_id": f"chunk_{idx:04d}",
            "source": source,
            "year": year,
            "section_title": section_title,
            "text": text,
            "grade_priority": grade_priority,
            "safety_critical": safety_critical,
        }

    def test_empty_chunks_returns_no_evidence_block(self):
        """Empty chunk list → no-evidence block with general knowledge instruction."""
        result = format_chunks_for_prompt([])
        assert "<clinical_context>" in result
        assert "No clinical evidence was retrieved" in result
        assert "general DSMES educator knowledge" in result

    def test_single_chunk_renders_correctly(self):
        """One chunk → [1] header + text."""
        chunk = self._make_chunk()
        result = format_chunks_for_prompt([chunk])
        assert "[1]" in result
        assert "RSSDI_2022" in result
        assert "2022" in result
        assert "Glycemic Targets" in result
        assert "HbA1c target < 7.0%" in result
        assert "<clinical_context>" in result
        assert "</clinical_context>" in result

    def test_five_chunks_numbered_1_to_5(self):
        """Five chunks → [1] through [5] all present."""
        chunks = [self._make_chunk(idx=i) for i in range(1, 6)]
        result = format_chunks_for_prompt(chunks)
        for n in range(1, 6):
            assert f"[{n}]" in result

    def test_safety_critical_chunk_has_caution_label(self):
        """safety_critical=True chunk gets the SAFETY CAUTION label."""
        chunk = self._make_chunk(safety_critical=True, grade_priority=5)
        result = format_chunks_for_prompt([chunk])
        assert "SAFETY CAUTION" in result
        assert "DO NOT RECOMMEND" in result

    def test_non_safety_critical_chunk_has_no_caution_label(self):
        """safety_critical=False chunk does NOT get the SAFETY CAUTION label."""
        chunk = self._make_chunk(safety_critical=False)
        result = format_chunks_for_prompt([chunk])
        assert "SAFETY CAUTION" not in result

    def test_grade_priority_shown_in_header(self):
        """Grade priority value appears in the chunk header."""
        chunk = self._make_chunk(grade_priority=3)
        result = format_chunks_for_prompt([chunk])
        assert "Grade Priority: 3" in result


# ─────────────────────────────────────────────────────────────────────────────
# Fallback constant sanity checks
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackConstants:

    def test_phase2_fallback_has_required_keys(self):
        """PHASE2_FALLBACK must have all required keys for the return dict contract."""
        required = {
            "text", "chunks_used", "condition_flags_active",
            "query_cache_hit", "reranker_scores", "_fallback", "_fallback_reason",
        }
        assert required.issubset(set(PHASE2_FALLBACK.keys()))

    def test_phase2_fallback_text_is_non_empty(self):
        assert PHASE2_FALLBACK_TEXT and len(PHASE2_FALLBACK_TEXT) > 20

    def test_phase2_constraint_fallback_text_is_non_empty(self):
        assert PHASE2_CONSTRAINT_FALLBACK_TEXT and len(PHASE2_CONSTRAINT_FALLBACK_TEXT) > 20

    def test_fallback_chunks_used_is_empty_list(self):
        assert PHASE2_FALLBACK["chunks_used"] == []

    def test_fallback_flag_is_true(self):
        assert PHASE2_FALLBACK["_fallback"] is True
