"""
tests/manual_phase1_test.py -- Live Phase 1 model behavior test

Tests whether the Cerebras llama-3.1-8b model (via phase1_runner) correctly:
  A) Asks clarifying questions when needed (context_sufficient=false)
  B) Does NOT ask clarifying questions for QDS 1, 2, 5
  C) Generates button / list / open format options
  D) Extracts profile signals from Kerala patient language
  E) Detects escalation triggers
  F) Detects mid-clarification resolution

Run:
    python tests/manual_phase1_test.py

Requires CEREBRAS_API_KEY in .env
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from engine.phase1_runner import run_phase1

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

pass_count = 0
fail_count = 0


def check(label, condition, actual=None, note=""):
    global pass_count, fail_count
    status = f"{GREEN}PASS{RESET}" if condition else f"{RED}FAIL{RESET}"
    print(f"  {status}  {label}")
    if not condition and actual is not None:
        print(f"        got: {actual}")
    if note:
        print(f"        note: {YELLOW}{note}{RESET}")
    if condition:
        pass_count += 1
    else:
        fail_count += 1


def section(title):
    print(f"\n{BOLD}{CYAN}{'-'*60}{RESET}")
    print(f"{BOLD}{CYAN}{title}{RESET}")
    print(f"{BOLD}{CYAN}{'-'*60}{RESET}")


def show_result(result):
    display = {
        "intent":       result.get("intent"),
        "qds_score":    result.get("qds_score"),
        "sufficient":   result.get("context_sufficient"),
        "mid_resolved": result.get("mid_clarification_resolved"),
        "_fallback":    result.get("_fallback"),
    }
    if result.get("clarifying_questions"):
        display["clarifying_questions"] = result["clarifying_questions"]
    sig = result.get("profile_signals", {})
    compact_sig = {k: v for k, v in sig.items() if v and v != "self" and v != []}
    if compact_sig:
        display["signals"] = compact_sig
    print(f"  {json.dumps(display, indent=4)}")


# =============================================================================
# TEST CASES
# =============================================================================

INTER_CALL_DELAY = 2.0   # seconds between API calls — prevents rate-limit during batch testing
                          # Not needed in production (calls arrive one at a time, naturally spaced)


async def call(message, turns=None):
    """Thin wrapper that adds a small delay before each call to avoid 429s in batch testing."""
    await asyncio.sleep(INTER_CALL_DELAY)
    return await run_phase1(message, turns or [])


async def run_all_tests():

    # ---- GROUP A: Should ask clarifying questions ----------------------------

    section("GROUP A -- Model SHOULD ask a clarifying question")

    # A1: Tablet timing -- needs to know which tablet (each drug class has different rules)
    print(f"\n{BOLD}A1. Tablet timing -- ambiguous drug class{RESET}")
    print("  Input: 'Should I take my white tablet before or after food?'")
    r = await call("Should I take my white tablet before or after food?")
    show_result(r)
    check("context_sufficient is False",
          r["context_sufficient"] is False, r["context_sufficient"])
    check("intent is taking_medication",
          r["intent"] == "taking_medication", r["intent"])
    check("qds_score is 3",
          r["qds_score"] == 3, r["qds_score"])
    check("has 1-2 clarifying questions",
          1 <= len(r["clarifying_questions"]) <= 2, len(r["clarifying_questions"]))
    check("not a fallback",
          r["_fallback"] is False, r["_fallback"])
    if r["clarifying_questions"]:
        cq = r["clarifying_questions"][0]
        check("question has 'text' key",   "text"   in cq, cq)
        check("question has 'format' key", "format" in cq, cq)
        check("format is 'buttons'",
              cq["format"] == "buttons", cq.get("format"),
              "buttons preferred for drug-class choice (2-3 options)")

    # A2: Foot burning -- needs wound check + duration before advice is safe
    print(f"\n{BOLD}A2. Foot burning -- complication concern (QDS 4){RESET}")
    print("  Input: 'My feet burn and tingle at night. Is this because of diabetes?'")
    r = await call("My feet burn and tingle at night. Is this because of diabetes?")
    show_result(r)
    check("context_sufficient is False",
          r["context_sufficient"] is False, r["context_sufficient"])
    check("intent is reducing_risks",
          r["intent"] == "reducing_risks", r["intent"])
    check("qds_score is 4",
          r["qds_score"] == 4, r["qds_score"])
    check("has clarifying question",
          len(r["clarifying_questions"]) >= 1, len(r["clarifying_questions"]))
    check("neuropathy detected in profile signals",
          "neuropathy_suspected" in r.get("profile_signals", {}).get("complications_mentioned", []),
          r.get("profile_signals", {}).get("complications_mentioned", []))
    if r["clarifying_questions"]:
        cq = r["clarifying_questions"][0]
        # Accept buttons (sensation type) or open (duration) -- both are valid first questions
        # The model may ask "How long?" (open) or "burning vs sharp?" (buttons) -- both are clinically correct
        check("format is 'buttons' or 'open' (sensation type or duration)",
              cq["format"] in ("buttons", "open"), cq.get("format"),
              "buttons = sensation type (burning/sharp/both); open = duration -- both acceptable")

    # A3: Insulin timing -- basal vs premixed have completely different rules
    print(f"\n{BOLD}A3. Insulin shift -- needs insulin type (basal vs premixed){RESET}")
    print("  Input: 'I take injection at night. My neighbour takes in morning. Which is better?'")
    r = await call("I take injection at night. My neighbour takes in morning. Which is better?")
    show_result(r)
    check("context_sufficient is False",
          r["context_sufficient"] is False, r["context_sufficient"])
    check("intent is taking_medication",
          r["intent"] == "taking_medication", r["intent"])
    check("insulin_user detected in profile signals",
          r.get("profile_signals", {}).get("insulin_user") is True,
          r.get("profile_signals", {}).get("insulin_user"))
    check("has clarifying question",
          len(r["clarifying_questions"]) >= 1, len(r["clarifying_questions"]))

    # A4: Blurry eyes -- sudden vs gradual, one vs both eyes determines urgency
    print(f"\n{BOLD}A4. Blurry eyes -- retinopathy concern{RESET}")
    print("  Input: 'My eyes have been blurry for two weeks.'")
    r = await call("My eyes have been blurry for two weeks.")
    show_result(r)
    check("context_sufficient is False",
          r["context_sufficient"] is False, r["context_sufficient"])
    check("qds_score is 4",
          r["qds_score"] == 4, r["qds_score"])
    check("retinopathy detected in profile signals",
          "retinopathy_suspected" in r.get("profile_signals", {}).get("complications_mentioned", []),
          r.get("profile_signals", {}).get("complications_mentioned", []))

    # ---- GROUP B: Should NOT ask questions -- proceed immediately ------------

    section("GROUP B -- Model should NOT ask questions (context_sufficient=true)")

    # B1: Pure general awareness -- QDS 1, same answer for everyone
    print(f"\n{BOLD}B1. QDS 1 -- General awareness: 'What is HbA1c?'{RESET}")
    r = await call("What is HbA1c?")
    show_result(r)
    check("context_sufficient is True",
          r["context_sufficient"] is True, r["context_sufficient"])
    check("qds_score is 1",
          r["qds_score"] == 1, r["qds_score"])
    check("no clarifying questions",
          r["clarifying_questions"] == [], r["clarifying_questions"])

    # B2: Personal relevance -- QDS 2, no active decision being made
    print(f"\n{BOLD}B2. QDS 2 -- Personal HbA1c result{RESET}")
    print("  Input: 'My HbA1c came back as 7.8. Is that okay?'")
    r = await call("My HbA1c came back as 7.8. Is that okay?")
    show_result(r)
    check("context_sufficient is True",
          r["context_sufficient"] is True, r["context_sufficient"])
    check("qds_score is 2",
          r["qds_score"] == 2, r["qds_score"])
    check("no clarifying questions",
          r["clarifying_questions"] == [], r["clarifying_questions"])

    # B3: DISTRESS -- QDS 5 fires on distress alone, NEVER block with a question
    print(f"\n{BOLD}B3. QDS 5 -- Insulin fear (distress alone fires QDS 5){RESET}")
    print("  Input: 'Doctor wants to start me on insulin. I am very scared.'")
    r = await call("Doctor wants to start me on insulin. I am very scared.")
    show_result(r)
    check("context_sufficient is True",
          r["context_sufficient"] is True, r["context_sufficient"],
          "CRITICAL: never block a distressed patient with a clarifying question")
    check("qds_score is 5",
          r["qds_score"] == 5, r["qds_score"])
    check("intent is healthy_coping",
          r["intent"] == "healthy_coping", r["intent"])
    check("no clarifying questions",
          r["clarifying_questions"] == [], r["clarifying_questions"])

    # B4: Kerala rice barrier -- most documented dietary barrier, generic advice always applies
    print(f"\n{BOLD}B4. QDS 3 sufficient -- Kerala rice barrier{RESET}")
    print("  Input: 'My family cannot eat without rice. What should I do at mealtimes?'")
    r = await call("My family cannot eat without rice. What should I do at mealtimes?")
    show_result(r)
    check("context_sufficient is True",
          r["context_sufficient"] is True, r["context_sufficient"],
          "Documented Kerala barrier -- portion guidance applies universally")
    check("intent is healthy_eating",
          r["intent"] == "healthy_eating", r["intent"])
    check("qds_score is 3",
          r["qds_score"] == 3, r["qds_score"])
    check("no clarifying questions",
          r["clarifying_questions"] == [], r["clarifying_questions"])

    # B5: Chaaya (sweet tea) -- single highest-yield dietary intervention in Kerala
    print(f"\n{BOLD}B5. QDS 3 sufficient -- Chaaya (sweet tea) question{RESET}")
    print("  Input: 'I drink 6 cups of chai with sugar daily. Should I stop completely?'")
    r = await call("I drink 6 cups of chai with sugar daily. Should I stop completely?")
    show_result(r)
    check("context_sufficient is True",
          r["context_sufficient"] is True, r["context_sufficient"])
    check("intent is healthy_eating",
          r["intent"] == "healthy_eating", r["intent"])
    check("no clarifying questions",
          r["clarifying_questions"] == [], r["clarifying_questions"])

    # B6: Foot wound with duration -- Risk Engine handles escalation, Phase 2 still runs
    print(f"\n{BOLD}B6. Foot wound with duration -- proceed (Risk Engine flags separately){RESET}")
    print("  Input: 'I have a small wound on my toe that is not healing for one week.'")
    r = await call("I have a small wound on my toe that is not healing for one week.")
    show_result(r)
    check("context_sufficient is True",
          r["context_sufficient"] is True, r["context_sufficient"],
          "Foot wound with duration -- Risk Engine assigns Tier 3, Phase 2 still runs")
    check("intent is reducing_risks",
          r["intent"] == "reducing_risks", r["intent"])
    check("foot_wound_present detected",
          "foot_wound_present" in r.get("profile_signals", {}).get("complications_mentioned", []),
          r.get("profile_signals", {}).get("complications_mentioned", []))

    # ---- GROUP C: Escalation detection --------------------------------------

    section("GROUP C -- Escalation triggers (intent = escalation_only)")

    # C1: Dose change request -- must NEVER go to Phase 2
    print(f"\n{BOLD}C1. Dose change request{RESET}")
    print("  Input: 'My sugar is too high. Should I take more insulin than my doctor said?'")
    r = await call("My sugar is too high. Should I take more insulin than my doctor said?")
    show_result(r)
    check("intent is escalation_only",
          r["intent"] == "escalation_only", r["intent"])
    check("context_sufficient is True",
          r["context_sufficient"] is True, r["context_sufficient"],
          "escalation_only always proceeds -- Risk Engine handles response directly")

    # C2: Tier 4 emergency -- BG > 300 + confusion
    print(f"\n{BOLD}C2. Tier 4 emergency -- very high sugar with confusion{RESET}")
    print("  Input: 'My sugar is 350. I feel dizzy and confused. What should I do?'")
    r = await call("My sugar is 350. I feel dizzy and confused. What should I do?")
    show_result(r)
    check("intent is escalation_only",
          r["intent"] == "escalation_only", r["intent"])
    check("context_sufficient is True",
          r["context_sufficient"] is True)

    # C3: Medication self-stop -- NOT escalation (Risk Engine flags separately per spec)
    print(f"\n{BOLD}C3. Medication self-stop -- QDS 3, NOT escalation_only{RESET}")
    print("  Input: 'I stopped my tablet when I started feeling okay. Was that right?'")
    r = await call("I stopped my tablet when I started feeling okay. Was that right?")
    show_result(r)
    check("intent is taking_medication (NOT escalation_only)",
          r["intent"] == "taking_medication", r["intent"],
          "Per spec: medication self-stop is QDS 3 taking_medication -- Risk Engine flags it")
    check("context_sufficient is True",
          r["context_sufficient"] is True, r["context_sufficient"])

    # ---- GROUP D: Profile signal extraction ---------------------------------

    section("GROUP D -- Profile signal extraction from Kerala patient language")

    # D1: CKD condition flag from creatinine mention
    print(f"\n{BOLD}D1. CKD signal -- creatinine mention{RESET}")
    print("  Input: 'Doctor said my creatinine is a little high. I also have diabetes.'")
    r = await call("Doctor said my creatinine is a little high. I also have diabetes.")
    show_result(r)
    check("ckd in condition_flags",
          "ckd" in r.get("profile_signals", {}).get("condition_flags", []),
          r.get("profile_signals", {}).get("condition_flags", []))
    check("context_sufficient is True (CKD path is clear)",
          r["context_sufficient"] is True)
    check("nephropathy_suspected in complications",
          "nephropathy_suspected" in r.get("profile_signals", {}).get("complications_mentioned", []),
          r.get("profile_signals", {}).get("complications_mentioned", []))

    # D2: Family member asking -- session_context
    print(f"\n{BOLD}D2. Family member context detection{RESET}")
    print("  Input: 'My husband has diabetes. His sugar keeps going up. What should he eat?'")
    r = await call("My husband has diabetes. His sugar keeps going up. What should he eat?")
    show_result(r)
    check("session_context is family_member_inquiry",
          r.get("profile_signals", {}).get("session_context") == "family_member_inquiry",
          r.get("profile_signals", {}).get("session_context"))
    check("context_sufficient is True",
          r["context_sufficient"] is True)

    # D3: Lay drug name -- 'yellow tablet' maps to sulfonylurea
    print(f"\n{BOLD}D3. Lay medication language: 'yellow tablet' -> sulfonylurea{RESET}")
    print("  Input: 'I take a yellow tablet in the morning. Is it safe to skip one day?'")
    r = await call("I take a yellow tablet in the morning. Is it safe to skip one day?")
    show_result(r)
    check("sulfonylurea in medications_mentioned",
          "sulfonylurea" in r.get("profile_signals", {}).get("medications_mentioned", []),
          r.get("profile_signals", {}).get("medications_mentioned", []))

    # D4: Lay belief -- bitter gourd should be QDS 1, not escalation
    print(f"\n{BOLD}D4. Kerala lay belief: bitter gourd (kaipakka) cure{RESET}")
    print("  Input: 'Can bitter gourd (kaipakka) cure diabetes?'")
    r = await call("Can bitter gourd cure diabetes?")
    show_result(r)
    check("qds_score is 1 (general awareness, lay belief)",
          r["qds_score"] == 1, r["qds_score"])
    check("context_sufficient is True",
          r["context_sufficient"] is True)
    check("intent is NOT escalation_only",
          r["intent"] != "escalation_only", r["intent"])

    # ---- GROUP E: Question format quality -----------------------------------

    section("GROUP E -- Question format quality (buttons / list / open)")

    # E1: Dizziness timing -- list format expected (multiple distinct meal timings)
    print(f"\n{BOLD}E1. Dizziness after meals -- expects list format with meal timings{RESET}")
    print("  Input: 'I feel dizzy after eating. Is it my diabetes?'")
    r = await call("I feel dizzy after eating. Is it my diabetes?")
    show_result(r)
    if r["clarifying_questions"]:
        cq = r["clarifying_questions"][0]
        check("has preset options",
              len(cq.get("options", [])) > 0, cq.get("options"))
        check("options count is between 2 and 10",
              2 <= len(cq.get("options", [])) <= 10, len(cq.get("options", [])))
        check("format is 'list' or 'buttons'",
              cq["format"] in ("list", "buttons"), cq["format"])
    else:
        # Some interpretations are QDS 2 (general symptom) -- that is also acceptable
        check("context_sufficient is True (proceeded without question)",
              r["context_sufficient"] is True, r["context_sufficient"],
              "Acceptable if model treats this as QDS 2 -- general symptom question")

    # E2: Open format -- complex distress should NOT get preset options
    print(f"\n{BOLD}E2. Complex distress -- QDS 5, no question at all{RESET}")
    print("  Input: 'Everything is going wrong -- my sugar, my BP, my weight. Don't know where to start.'")
    r = await call("Everything is going wrong -- my sugar, my BP, my weight. Don't know where to start.")
    show_result(r)
    check("QDS 5 -- context_sufficient True, no clarifying question",
          r["context_sufficient"] is True and r["clarifying_questions"] == [],
          (r["context_sufficient"], r["clarifying_questions"]),
          "Multiple compounding concerns fires QDS 5 -- never ask a clarifying question")

    # ---- GROUP F: Mid-clarification detection -------------------------------

    section("GROUP F -- Mid-clarification resolution")

    # F1: Patient answers the bot's prior question
    print(f"\n{BOLD}F1. Patient answers prior clarifying question{RESET}")
    prior = [
        {"role": "patient", "content": "My feet burn and tingle at night."},
        {"role": "bot",     "content": "Is the sensation more burning and tingling, or sharp pain?"},
    ]
    print("  Prior bot turn: 'Is the sensation more burning and tingling, or sharp pain?'")
    print("  Current input:  'It is burning and tingling. Has been 3 months now.'")
    r = await call("It is burning and tingling. Has been 3 months now.", prior)
    show_result(r)
    check("mid_clarification_resolved is True",
          r["mid_clarification_resolved"] is True, r["mid_clarification_resolved"],
          "CRITICAL: patient answered the bot's question -- must not ask another")
    check("context_sufficient is True (forced by resolution)",
          r["context_sufficient"] is True, r["context_sufficient"])
    check("no further clarifying questions",
          r["clarifying_questions"] == [], r["clarifying_questions"])

    # F2: Patient pivots to new topic -- should NOT set mid_clarification_resolved
    print(f"\n{BOLD}F2. Patient pivots to new topic after bot's question{RESET}")
    prior2 = [
        {"role": "patient", "content": "My feet burn and tingle at night."},
        {"role": "bot",     "content": "Is the sensation more burning and tingling, or sharp pain?"},
    ]
    print("  Prior bot turn: 'Is the sensation more burning and tingling, or sharp pain?'")
    print("  Current input:  'Actually, I want to ask about diet. Can I eat matta rice?'")
    r = await call("Actually, I want to ask about diet. Can I eat matta rice?", prior2)
    show_result(r)
    check("mid_clarification_resolved is False (pivot detected)",
          r["mid_clarification_resolved"] is False, r["mid_clarification_resolved"],
          "Patient pivoted to new topic -- treat as fresh message")
    check("intent is healthy_eating (new question classified correctly)",
          r["intent"] == "healthy_eating", r["intent"])

    # ---- Summary ------------------------------------------------------------

    total = pass_count + fail_count
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}RESULTS:  {GREEN}{pass_count} PASS  |  {RED}{fail_count} FAIL  |  {total} total{RESET}")
    print(f"{BOLD}{'='*60}{RESET}\n")

    if fail_count > 0:
        print(f"{YELLOW}Some tests failed. This usually means:{RESET}")
        print("  1. Model is inconsistent on this case -- re-run to check if it's flaky")
        print("  2. System prompt needs a more explicit example for this case")
        print("  3. llama-3.1-8b has a genuine limitation here (switch to qwen-3-235b)")
        print()


if __name__ == "__main__":
    if not os.environ.get("CEREBRAS_API_KEY"):
        print(f"{RED}ERROR: CEREBRAS_API_KEY not set. Add it to .env first.{RESET}")
        sys.exit(1)

    asyncio.run(run_all_tests())
