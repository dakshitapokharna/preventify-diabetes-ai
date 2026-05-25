# Phase 1 — Context Engine: Build Specification

**Version:** 0.3  
**Date:** 2026-05-24  
**Status:** All 6 items 🟢 DONE — Phase 1 code can begin  
**Authoritative architecture:** `BOT_CONVERSATION_ARCHITECTURE.md` Section 6  
**Build order reference:** `BOT_CONVERSATION_ARCHITECTURE.md` Section 15, Step 4  

---

## What This Document Is

`BOT_CONVERSATION_ARCHITECTURE.md` defines *what* Phase 1 does and *why*.  
This document defines *how to build it* — the implementation-ready layer that is currently missing.

Six things are missing before Phase 1 (Context Engine) can be coded:

| # | Item | Status | Blocks |
|---|------|--------|--------|
| 1 | Phase 1 system prompt | 🟢 DONE — prompt locked, clinical sign-off deferred to production gate | Phase 1 code |
| 2 | `profile_signals` JSON schema | 🟢 DONE — schema + vocabulary locked | DB schema, signal writer |
| 3 | Mid-clarification session state | 🟢 DONE — Option C, prompt + query_builder built | Phase 1 code |
| 4 | Error fallback for malformed output | 🟢 DONE — `validate_phase1_output()` + 37/37 tests passing | Phase 1 code |
| 5 | Risk tier → Phase 1 merge | 🟢 DONE — `response_formatter.py` locked, clinical sign-off deferred to production gate | Response formatter |
| 6 | `base_model_spec.md` superseded | 🟢 DONE | — |

**Files created this session:**
- `prompts/phase1_system_prompt.txt` — ~2,700 tokens (well above 1,024 cache minimum; updated to add `<intent_guide>`, full medication vocab examples, and complete JSON output example)
- `schemas/phase1_schema.py` — Python schema + `PHASE1_FALLBACK` + `validate_phase1_output()`
- `schemas/phase1_output_schema.json` — JSON schema with `propertyOrdering` for Gemini 2.0 Flash
- `engine/query_builder.py` — `build_phase2_query()` for all 3 retrieval paths (Item 3)

**Decisions locked this session:**
- Intent enum: ADCES7-mapped (9 values — listed in `<intent_guide>` block in prompt and in schema)
- QDS examples: 15 annotated Kerala patient language examples across QDS 1–5
- Mid-clarification state: Option C — Phase 1 detects from conversation context; upgrade to DB-backed for production
- Medication vocabulary: 11 controlled terms, all with patient-language examples in prompt (see `schemas/phase1_schema.py` → `MEDICATION_VOCABULARY`)
- Complication vocabulary: 6 controlled terms (see `schemas/phase1_schema.py` → `COMPLICATION_VOCABULARY`)
- `propertyOrdering` confirmed required for Gemini 2.0 Flash — included in both schema files
- QDS 5 boundary: emotional signal alone (fear, distress, helplessness) IS sufficient for QDS 5
- validate_phase1_output() overrides: QDS 1, 2, 5 always context_sufficient=True; escalation_only always context_sufficient=True

**Research sources used:**
- [Voices of Care — Kerala T2DM/HTN patient study, Malappuram 2022–23](https://pmc.ncbi.nlm.nih.gov/articles/PMC11155455/) — real patient quotes for QDS examples
- [South Asian diabetes barriers systematic review](https://pmc.ncbi.nlm.nih.gov/articles/PMC4575130/) — medication stigma, lay language
- [Gemini structured output docs](https://ai.google.dev/gemini-api/docs/structured-output) — propertyOrdering, response_mime_type, enum schema
- [Gemini context caching docs](https://ai.google.dev/gemini-api/docs/caching) — 1,024 token minimum confirmed, TTL API
- [Gemini prompting strategies](https://ai.google.dev/gemini-api/docs/prompting-strategies) — few-shot examples, XML delimiters, classification best practices
- [ADCES7 self-care behaviors](https://www.adces.org/diabetes-education-dsmes) — educator scope, intent enum grounding

Work items in order. Each section explains what the item is, what research is needed before writing, and what the output looks like.

---

## Item 1 — Phase 1 System Prompt (~1,100 tokens)

### What It Is

The system prompt is the **only instruction Gemini 2.0 Flash receives**. It defines everything Phase 1 does. Without it, Phase 1 cannot be built.

The prompt must be ≥1,024 tokens to qualify for Gemini context caching (hard minimum enforced by the API). Target is ~1,100 tokens. At 0.25× cache-hit input price vs 1.0× standard, this saves ~75% on every Phase 1 call after the first in a session. A prompt shorter than 1,024 tokens fails the cache write silently — turns pay full price every time.

The cached block = system prompt + QDS rubric + annotated examples. These serve dual purpose: cache qualification AND improved QDS accuracy.

### Deliverable

🟢 **Built:**
- `prompts/phase1_system_prompt.txt` — ~2,218 tokens (confirmed via `len(text)//4`). Sections: `<role>`, `<qds_rubric>` (15 annotated Kerala examples), `<sufficiency_rules>`, `<clarification_format_guide>`, `<profile_signals_guide>`, `<edge_cases>`, `<output_schema>`
- `schemas/phase1_schema.py` — `PHASE1_RESPONSE_SCHEMA` dict with `propertyOrdering`, `PHASE1_FALLBACK`, `validate_phase1_output()`
- `schemas/phase1_output_schema.json` — standalone JSON schema for reference and non-Python consumers

🟢 **Item 1 locked. Clinical sign-off deferred to production gate (D7).**

The prompt is literature-grounded and ships as-is. Dr. Rakesh QDS validation (50 real patient questions) and Kerala brand name confirmation are **production-gate items** — they do not block Phase 1 code. Schedule them before the base model goes live with real patients.

Deferred items (to schedule before production launch):
- **Dr. Rakesh QDS validation** — run 50 real patient questions through the prompt, check classification accuracy. D7 gate in BOT_CONVERSATION_ARCHITECTURE.md Section 14.
- **Kerala brand name confirmation** — Glycomet, Amaryl, Januvia/Galvus, Farxiga/Jardiance, Ozempic/Victoza, Lantus/Basaglar, Novomix/Mixtard in the prompt. Dr. Rakesh to confirm these match what Kerala T2DM patients actually receive.

**Research grounding added to the prompt (not in previous version):**
- QDS 1: bitter gourd / jaggery lay beliefs (documented Kerala/South Asian patterns)
- QDS 2: raw glucometer reading presentation ("My fasting is 168"), treatment inefficacy concern
- QDS 3: rice barrier ("family can't eat without rice"), chaaya harm-reduction, symptom-guided dosing
- QDS 4: discovery moment examples ("could this be from my diabetes?"), Kerala lay terms for vision ("dull and not bright"), creatinine as CKD signal
- QDS 5: medicine-stopping fear + kidney conflation, Gulf-migrant remote caregiver distress
- QDS 5 rationale added: 44% distress prevalence in Indian T2DM, OR 2.94 for non-adherence, confirms distress-alone fires QDS 5
- profile_signals_guide: expanded neuropathy lay language (pricking, feet go to sleep), vision lay language (sugar gone to eyes), creatinine as ckd signal

**Key research finding — QDS 5 boundary confirmed:**
Distress alone (without clinical complexity) is a valid QDS 5 trigger. Evidence: 44% pooled prevalence of clinically significant diabetes distress in South Asian T2DM patients (PMC12362461); distress is the strongest independent predictor of medication non-adherence (OR 2.94, PMC5523532); insulin fear documented in 68.9% of Indian patients (PMC7113951). The QDS 5 definition is clinically defensible.

**Note — QDS framework is novel, deferred clinical validation:**
No published analog to the QDS 1–5 depth-scoring system exists in diabetes education literature. Dr. Rakesh's 50-question validation remains a **production gate** (D7), not a build blocker. Phase 1 code proceeds with the literature-grounded prompt as-is.

**Research sources:**
- [Voices of Care — Kerala/Malappuram T2DM patients (PMC11155455)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11155455/)
- [South Asian diabetes patient barriers (PMC4575130)](https://pmc.ncbi.nlm.nih.gov/articles/PMC4575130/)
- [Insulin fear — Indian patients, Bangalore (PMC7113951)](https://pmc.ncbi.nlm.nih.gov/articles/PMC7113951/)
- [Insulin acceptance — South/SE Asian patients (PMC4231611)](https://pmc.ncbi.nlm.nih.gov/articles/PMC4231611/)
- [Diabetes distress meta-analysis, South Asia (PMC12362461)](https://pmc.ncbi.nlm.nih.gov/articles/PMC12362461/)
- [Distress + non-adherence, coastal South India (PMC5523532)](https://pmc.ncbi.nlm.nih.gov/articles/PMC5523532/)
- [Diabetic retinopathy patient language, India (PMC10270603)](https://pmc.ncbi.nlm.nih.gov/articles/PMC10270603/)
- [Gulf-migrant family caregiver diabetes support (PMC11181519)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11181519/)
- [Kerala low-calorie diet — rice/coconut quotes (PMC9552705)](https://pmc.ncbi.nlm.nih.gov/articles/PMC9552705/)
- [DPN patient experience — neuropathy lay language (PMC12587864)](https://pmc.ncbi.nlm.nih.gov/articles/PMC12587864/)

---

### Research Area 1a — Role Framing

Phase 1 must understand its role is **classification only** — not answering the patient. Gemini must not try to generate a clinical response in Phase 1. The role framing must make this clear.

Draft (to be refined with clinical input):

```
You are a context classification engine for a diabetes education chatbot
serving patients in Kerala, India. Your ONLY job is:

1. Understand what the patient is asking and why
2. Assign a QDS score (1–5) based on question depth
3. Decide if sufficient context exists to retrieve a clinical answer
4. If not: generate the minimum clarifying questions needed
5. Extract any clinical or personal signals mentioned

You output ONLY valid JSON. You never generate a patient-facing clinical
response. You never answer the health question yourself. The actual clinical
answer is generated separately — your output is used to decide whether to
generate it now or ask a clarifying question first.
```

**What needs to be decided:**
- Is this role framing clear enough to prevent Gemini from generating clinical content?
- Should the prompt explicitly list what it must NOT do (don't answer, don't diagnose)?
- Test: run 10 patient messages through this prompt and check that output is always JSON only

---

### Research Area 1b — QDS Rubric with 20–25 Annotated Examples

`BOT_CONVERSATION_ARCHITECTURE.md` Section 6.2 has one example per score level (5 total). Need 20–25 annotated examples to:

- Hit the 1,100-token cache target (5 examples ≈ 50 tokens; need ~1,000 tokens of examples)
- Cover Kerala patient language patterns (lay terms, not clinical terminology)
- Cover edge cases that would be misclassified without guidance
- Improve LLM scoring accuracy before Dr. Rakesh's 50-question validation (D7 in BOT_CONVERSATION_ARCHITECTURE.md Section 14)

**Target examples per score:**

| QDS | Count | Focus |
|-----|-------|-------|
| 1 | 4–5 | Pure awareness, no personal stake, same answer for everyone |
| 2 | 4–5 | Personal relevance — "my" situation, not making a decision |
| 3 | 5–6 | Active management decisions; includes misdirected dose questions the bot must redirect |
| 4 | 4–5 | Complication concern in lay language — numb feet, blurry eyes, kidney worry |
| 5 | 3–4 | Distressed, scared, multiple compounding concerns, emotional signals |

**Each example must include:**
```
Patient says: "<message in likely patient language>"
QDS: <score>
Why: <1-line reasoning — what makes this score and not the adjacent one>
Context sufficient: <yes/no>
If no — ask: "<clarifying question text>"
Format: <buttons|list|open>
Options (if buttons/list): ["<option 1>", "<option 2>", ...]
```

**Examples that need to be written (seed list — expand this):**

*QDS 1 — General awareness:*
- "What is HbA1c?"
- "What does fasting blood sugar mean?"
- "Is diabetes hereditary?"
- "What foods raise blood sugar?"
- "What is the normal sugar level?"

*QDS 2 — Personal relevance:*
- "My HbA1c came back 7.8 — is that okay?"
- "My fasting sugar is 135. My doctor said it's borderline. Should I worry?"
- "I gained 5 kg in the last 6 months. Is that related to my sugar?"
- "My father has diabetes. Am I at risk?"

*QDS 3 — Active management:*
- "Should I take my tablet before or after food?"  ← requires: which tablet?
- "Doctor said my sugar is going up again. What should I change?"  ← QDS 3 NOT 1 — active management failure signal
- "I heard rice is bad for diabetes. Should I stop rice completely?"
- "I take injection at night. Can I shift it to morning?"  ← requires: which insulin?
- "I missed my tablet today. Should I take double dose tomorrow?"  ← redirect — do not advise dose

*QDS 4 — Complication concern:*
- "My feet go numb at night"  ← clarify: burning/tingling vs sharp vs swelling?
- "My eyes have been blurry for 2 weeks"  ← clarify: sudden or gradual? one eye or both?
- "Doctor said my creatinine is high. What does that mean for my diabetes?"
- "My legs swell up by evening"
- "I have a small wound on my toe that's not healing"  ← Tier 3 risk — escalate

*QDS 5 — Distressed / complex:*
- "My doctor wants to start me on insulin. I'm scared."  ← answer immediately, no clarifying question
- "I've been diabetic for 15 years. Now my kidneys are also affected. I don't know what to eat anymore."
- "My sugar is always high no matter what I do. I feel like giving up."
- "Everything is going wrong — my sugar, my BP, my weight. I don't know where to start."

**What needs clinical input (Dr. Rakesh):**
- Are the QDS 1/2 distinction examples correct? (General awareness vs personal relevance)
- Are the QDS 3/4 examples realistic for Kerala patients?
- Does QDS 5 boundary feel right? (Is "I'm scared" alone enough for QDS 5?)
- Any Kerala-specific expressions or situations missing from the list?

---

### Research Area 1c — JSON Output Schema (Exact Format in Prompt)

The JSON schema must appear verbatim in the system prompt so Gemini outputs parseable JSON every time. This is the complete proposed schema:

```json
{
  "intent": "<intent_enum_value>",
  "qds_score": 3,
  "context_sufficient": false,
  "clarifying_questions": [
    {
      "text": "Which tablet do you take for diabetes?",
      "format": "buttons",
      "options": ["Metformin (white tablet)", "Insulin injection", "Not sure of the name"]
    }
  ],
  "profile_signals": {
    "diabetes_type": null,
    "medications_mentioned": ["oral_antidiabetic_unspecified"],
    "insulin_user": null,
    "condition_flags": [],
    "complications_mentioned": [],
    "location_hint": null,
    "session_context": "self"
  },
  "mid_clarification_resolved": false
}
```

**Schema rules (must also be in the prompt):**
- `clarifying_questions` is `[]` when `context_sufficient` is `true`
- `options` is `[]` when `format` is `"open"`
- `options` has 2–3 items when `format` is `"buttons"` (max 3 — WhatsApp hard limit)
- `options` has 2–10 items when `format` is `"list"`
- Never generate more than 2 clarifying questions per turn
- `mid_clarification_resolved` is `true` when the current patient message is answering a clarifying question the bot asked in the previous turn — in this case `context_sufficient` must always be `true`

---

### Research Area 1d — Intent Enum

`BOT_CONVERSATION_ARCHITECTURE.md` defines `intent: string` but never lists valid values. `base_model_spec.md` has a partial list. This is the reconciled proposed enum:

```
nutrition_education        Food, diet, carb content, GI, portion sizes, meal timing
drug_education             Medication mechanism, side effects, storage, missed dose (redirect dose Q)
symptom_query              Sensations, physical complaints, new symptoms
monitoring                 Blood glucose, HbA1c, SMBG, CGM, how often to test
complication_screening     Foot, eye, kidney, heart, nerve concerns — may overlap symptom_query
fasting_protocol           Ramadan, Ekadashi, Navratri, Lent, other religious fasting
lifestyle_education        Exercise, weight, stress, sleep, smoking
escalation_only            Question requires clinical judgment beyond DSMES scope — always escalate
general_dsmes              Catch-all — general diabetes self-management, does not fit above
```

**What needs to be decided:**
- Should `complication_screening` and `symptom_query` merge? (Both often trigger Tier 2 sources)
- Is `fasting_protocol` specific enough or should it be `ramadan` + `other_fasting` separately?
- Does Phase 2's metadata pre-filter actually use the `intent` field? (Section 7.3 uses condition flags, not intent — verify this gap)

**Current gap:** Section 7.3 (Metadata Pre-filter) uses `retrieval_tier` and `condition_trigger` — it does NOT filter by `intent`. If `intent` is not used downstream, why is it in the Phase 1 output? Clarify before finalizing.

---

### Research Area 1e — Edge Case Rules in the Prompt

These rules must appear explicitly in the system prompt, not just in this spec:

```
RULE: QDS 5 (distressed patient)
  → Always set context_sufficient: true
  → Never generate clarifying questions
  → Answer immediately — asking questions when a patient is distressed is harmful

RULE: Potential Tier 4 keywords detected (chest pain, unconscious, sugar < 70, 
       DKA signs, foot wound, self-harm)
  → Set intent: "escalation_only", context_sufficient: true
  → The risk engine will handle the response — do not generate questions

RULE: Non-diabetes question (unrelated topic)
  → Set intent: "general_dsmes", qds_score: 1, context_sufficient: true
  → Bot will gently redirect in Phase 2

RULE: Family member asking about relative's diabetes
  → Set session_context: "family_member_inquiry" in profile_signals
  → Answer as if the relative is the patient
  → QDS scoring is based on the question, not who asked it

RULE: Mid-clarification turn (patient answering previous bot question)
  → Detect from conversation context: bot's last message was a question
  → Set mid_clarification_resolved: true, context_sufficient: true
  → Do NOT ask another clarifying question — proceed to Phase 2
  → Do NOT re-assess QDS on the answer alone; QDS carries forward from original question

RULE: Patient provides partial or evasive answer to clarifying question
  → Still set mid_clarification_resolved: true
  → Proceed to Phase 2 with whatever context is available
  → Never ask a third round of clarifying questions
```

---

## Item 2 — `profile_signals` JSON Schema

### What It Is

The formal contract defining every field that Phase 1's `profile_signals` object outputs and how those fields map to the Neon DB `users` table.

This is needed before:
- Neon DB `users` table schema is finalized (Step 2 in build order)
- Phase 1 signal writer function is coded
- Phase 2 query enrichment is coded (it reads stored signals)

### Deliverable

🟢 **All built:**
- `schemas/profile_signals_schema.json` — formal JSON schema with `$db_column`, `$db_type`, `$merge_rule` annotations per field
- `schemas/users_table.sql` — full DDL: `users`, `session_turns`, `query_cache` tables + indexes + `updated_at` trigger
- `engine/signal_writer.py` — `write_profile_signals()` (asyncpg, async pipeline) + `write_profile_signals_sync()` (psycopg2, tests); all merge rules implemented: `_upgrade_diabetes_type()`, `_merge_array()`, `_merge_location()`
- `BOT_CONVERSATION_ARCHITECTURE.md` Section 6.5 — replaced informal table with formal schema reference + updated merge rules table
- `schemas/phase1_schema.py` — `erectile_dysfunction_mentioned` added to `COMPLICATION_VOCABULARY` (was in spec, missing from validator)

---

### Research Area 2a — Complete Field Mapping

Every `profile_signals` field must map to a `users` table column with a defined merge rule:

| Phase 1 outputs | DB column | Type | Merge rule |
|----------------|-----------|------|-----------|
| `diabetes_type` | `users.diabetes_type` | TEXT | One-way upgrade only: `null → suspected → prediabetes → T2DM / T1DM / GDM`. Never downgrade. |
| `medications_mentioned[]` | `users.medications_mentioned` | TEXT[] | Append only. No duplicates. New vocabulary terms added, existing never removed. |
| `insulin_user` | `users.insulin_user` | BOOLEAN | Set `true` only, never reset to `false`. Once insulin mentioned, always flagged. |
| `condition_flags[]` | `users.condition_flags` | TEXT[] | Append only — flags are **permanent**. See Section 9.1 of BOT_CONVERSATION_ARCHITECTURE.md. |
| `complications_mentioned[]` | `users.complications_mentioned` | TEXT[] | Append only. No duplicates. |
| `location_hint` | `users.location_hint` | TEXT | Overwrite if new value is more specific (city > district > state). |
| `session_context` | Session memory only | — | NOT persisted to DB. Clears at session end. |

**Merge rule for `diabetes_type` upgrade path:**
```
null → "diabetes_suspected" → "prediabetes" → "T2DM" / "T1DM" / "GDM"
         (patient says                (doctor told           (explicitly stated)
          "sugar problem")             "borderline")
```

---

### Research Area 2b — Medication Controlled Vocabulary

When a patient says "white tablet in morning" or "injection at night," Phase 1 must map this to a controlled term. The controlled vocabulary must be in the system prompt so Gemini uses consistent terms.

**Proposed medication vocabulary:**

```
oral_antidiabetic_unspecified    "I take one tablet" / "white tablet" — drug name unknown
metformin                        explicitly named
sulfonylurea                     glipizide / glimepiride / glyburide — named or described as "yellow tablet"
dpp4_inhibitor                   sitagliptin / vildagliptin / alogliptin — named
sglt2_inhibitor                  dapagliflozin / empagliflozin / canagliflozin — named
glp1_ra                          semaglutide / liraglutide — named or described as "weekly injection"
insulin_basal                    "long-acting injection" / "night injection"
insulin_premixed                 "mixed insulin" / "30/70"
insulin_unspecified              "I take injection" — type unknown
bp_tablet_unspecified            antihypertensive — name unknown
statin_unspecified               "cholesterol tablet" — name unknown
```

**What needs clinical input:**
- Are there common brand names in Kerala that Phase 1 should recognize? (e.g., "Glycomet" = metformin, "Glucobay" = acarbose)
- Should `sulfonylurea` be split into specific drugs or kept as a class?

---

### Research Area 2c — Complication Vocabulary

Controlled vocabulary for `complications_mentioned`:

```
neuropathy_suspected             "feet go numb" / "burning in legs" / "tingling"
retinopathy_suspected            "blurry vision" / "vision changes" / "floaters"
nephropathy_suspected            "protein in urine" / "doctor said kidney issue" 
                                  (before ckd condition_flag is set)
foot_wound_present               "wound on foot" / "sore not healing" / "ulcer"
                                  → ALSO triggers Tier 3 risk in Risk Engine
autonomic_suspected              "dizzy when standing" / "gut problems" / "sweating at night"
erectile_dysfunction_mentioned   sensitive — log but never surface in response
```

**Note:** `foot_wound_present` in `complications_mentioned` AND a `condition_flags: ["ckd"]` detection should independently each trigger higher retrieval weight — but they are logged separately. The Risk Engine also independently scans for foot wound language and may assign Tier 3.

---

## Item 3 — Mid-Clarification Session State

### What It Is

A mechanism so Phase 1 knows when the current patient message is answering a previous clarifying question (not a new question). Without it, Phase 1 treats every message identically and may ask a second round of clarifying questions on a patient who already answered one.

### Research Area 3a — State Storage Decision

Three options:

**Option A — DB-backed state on `session_turns` table:**
```sql
-- Add to session_turns table (Step 2 in build order):
ALTER TABLE session_turns 
  ADD COLUMN is_clarification_prompt BOOLEAN DEFAULT FALSE,
  ADD COLUMN clarification_for_message_id TEXT;
-- When bot sends clarifying question, log turn with is_clarification_prompt=TRUE
-- Phase 1 receives this flag when Session Manager loads last 5 turns
```
*Pro:* Survives server restarts. Survives multi-server setups.  
*Con:* Adds DB read on every turn to check this flag.

**Option B — Application-layer session dict:**
```python
# In-memory session state, reset on session end
session_state[user_id] = {
    "pending_clarification": {
        "original_message": "my feet go numb",
        "original_qds": 4,
        "question_asked": "Is it more burning/tingling, or sharp pain?",
        "question_format": "buttons"
    }
}
# If None — this is a fresh question, not an answer
```
*Pro:* Zero DB cost, fast.  
*Con:* Lost on server restart. Not suitable for distributed deployment.

**Option C — Conversation context detection by Phase 1:**
```
# No separate state. Phase 1 reads last 5 turns.
# If bot's last message was a question → this turn is the answer.
# Phase 1 detects this from conversation flow and sets mid_clarification_resolved: true.
```
*Pro:* Zero additional state. No schema change. Simplest build.  
*Con:* Relies on Phase 1 (an LLM) to detect this reliably — adds failure mode.

**Recommendation:** Start with **Option C** during testing phase (simplest, fewest moving parts). Switch to Option A when moving to production (reliability over simplicity for SaMD Class B).

**✅ DECISION MADE: Option C chosen for testing phase.**

Rationale:
- Zero schema changes required — `session_turns` table already stores the full prior exchange
- The prompt's `<edge_cases>` block (MID-CLARIFICATION DETECTION rule) is already in `prompts/phase1_system_prompt.txt`
- The LLM reliably detects "bot's last message was a question + patient is now answering" from 5 turns of context — validated in prompt testing
- Production migration to Option A: when moving off single-server testing, add `is_clarification_prompt BOOLEAN DEFAULT FALSE` to `session_turns` and update the Session Manager to pass it to Phase 1 as explicit state. This requires no changes to Phase 1's output schema — `mid_clarification_resolved` remains the same field.

---

### Research Area 3b — Merged Query Construction for Phase 2

**✅ BUILT: `engine/query_builder.py` → `build_phase2_query()`**

When `mid_clarification_resolved: true`, Phase 2 must not search on the patient's one-word clarification answer alone. It needs the combined context from the original question + the answer.

**How it works with Option C:**

With Option C, no `is_clarification_answer` flag exists in session memory — Phase 1 detects the mid-clarification state from conversation context. The session_turns pattern at this point is always:

```
session_turns = [
    ...,
    {"role": "patient", "content": "my feet go numb at night"},   ← original question
    {"role": "bot",     "content": "Is it burning/tingling..."},   ← bot's clarifying Q
]
current_message = "burning and tingling in both feet"             ← patient's answer
```

`build_phase2_query()` walks backward through `session_turns` to find the last patient turn — that is the original question. It merges:

```
"my feet go numb at night — clarification: burning and tingling in both feet"
```

Profile enrichment is then applied if the patient is a returning user:

```
"my feet go numb at night — clarification: burning and tingling in both feet
 [Patient context: T2DM; on metformin]"
```

**Resolved open questions:**

| Question | Decision |
|----------|----------|
| Does the enriched query replace the original or append? | **Replace entirely.** The merged string `original — clarification: answer` is the complete retrieval query. The original question alone is not sent. |
| How is `is_clarification_answer` set with Option C? | **It is not.** Option C uses no such flag. `_build_mid_clarification_query()` finds the prior patient turn by walking backward through `session_turns` — no DB flag required. |
| Where in the pipeline is this built? | **Phase 2 Step 1, before embedding.** `build_phase2_query()` is the first call in Phase 2, before `bge-large-en-v1.5` encoding. It lives in `engine/query_builder.py`. |

**Full function reference:** `engine/query_builder.py` → `build_phase2_query(current_message, session_turns, profile, mid_clarification_resolved)`

---

### Research Area 3c — Multi-Turn Clarification Limit

**Hard rule (proposed):** After one round of clarification (one Q&A exchange), Phase 1 always routes to Phase 2. Never a second round of clarifying questions.

Rationale: Two rounds of questions before getting an answer feels like an interrogation. The bot should answer with imperfect context rather than frustrate the patient.

**Edge case:** What if the patient's clarification answer is itself a new question?  
Example — Bot: "Is it burning or sharp pain?" Patient: "Actually, my bigger concern is whether I can eat rice."

- Treat as new message
- Do not treat as clarification answer
- Phase 1 processes fresh, assigns new QDS, new context check

**Detection:** If Phase 1 detects `mid_clarification_resolved: false` despite bot's last turn being a question, it means the patient pivoted. Proceed normally.

---

## Item 4 — Error Fallback for Malformed Phase 1 Output

### What It Is

Phase 1 calls Gemini 2.0 Flash via API. The API can fail — timeout, rate limit, malformed JSON, or missing required fields. The system must behave deterministically on any failure. For SaMD Class B, "the bot crashed" is not an acceptable failure mode.

### Research Area 4a — Failure Taxonomy

| Failure type | How detected | Action |
|-------------|-------------|--------|
| Network timeout (> 3s) | `asyncio.TimeoutError` | Apply fallback defaults |
| Rate limit (HTTP 429) | `google.api_core.exceptions.ResourceExhausted` | Retry once after 1s, then fallback |
| Model unavailable (HTTP 503) | `ServiceUnavailable` exception | Fallback + ops alert |
| Non-JSON response text | `json.JSONDecodeError` | Fallback |
| Valid JSON, missing `qds_score` | Schema validation fails | Use defaults for missing fields |
| Valid JSON, missing `context_sufficient` | Schema validation fails | Default to `true` (never block patient) |
| `qds_score` outside 1–5 range | Value validation fails | Default to 2 |

---

### Research Area 4b — Fallback Defaults

```python
PHASE1_FALLBACK = {
    "intent": "general_dsmes",
    "qds_score": 2,                   # moderate depth — conservative default
    "context_sufficient": True,       # always proceed to Phase 2 on failure
    "clarifying_questions": [],       # never ask clarifying questions on fallback
    "profile_signals": {              # no signal extraction on failure
        "diabetes_type": None,
        "medications_mentioned": [],
        "insulin_user": None,
        "condition_flags": [],
        "complications_mentioned": [],
        "location_hint": None,
        "session_context": "self"
    },
    "mid_clarification_resolved": False,
    "_fallback": True,               # internal flag — never shown to patient
    "_fallback_reason": ""           # filled with error type for logging
}
```

**Rationale for `context_sufficient: True`:** It is always safer to let Phase 2 run (with its own safety constraints) than to block the patient on a clarifying question the system cannot track. A generic Phase 2 response is better than no response.

**Rationale for `qds_score: 2`:** Conservative middle ground. Avoids triggering lead capture (would need higher scores) while still incrementing the session count.

---

### Research Area 4c — Retry and Logging Logic

```python
async def run_phase1(message: str, session: dict, max_retries: int = 1) -> dict:
    for attempt in range(max_retries + 1):
        try:
            raw = await gemini_flash_cached.generate_content_async(
                contents=[...],
                generation_config={"response_mime_type": "application/json"},
                request_options={"timeout": 3.0}
            )
            parsed = json.loads(raw.text)
            validated = validate_phase1_schema(parsed)   # fills defaults for missing fields
            return validated
        
        except (TimeoutError, ResourceExhausted) as e:
            if attempt < max_retries:
                await asyncio.sleep(1.0)
                continue
            fallback = {**PHASE1_FALLBACK, "_fallback_reason": type(e).__name__}
            log_phase1_failure(message, session, fallback)
            return fallback
        
        except (JSONDecodeError, ValidationError) as e:
            fallback = {**PHASE1_FALLBACK, "_fallback_reason": f"parse_error:{type(e).__name__}"}
            log_phase1_failure(message, session, fallback)
            return fallback
```

**Logging schema** — every failure logged to `logs/phase1_failures.jsonl`:
```json
{
  "timestamp": "2026-05-23T10:32:01Z",
  "user_id": "uuid-here",
  "message_id": "msg-uuid",
  "error_type": "JSONDecodeError",
  "raw_output": "<whatever Gemini actually returned>",
  "fallback_applied": true,
  "attempt_count": 1
}
```

**Ops review:** Phase 1 failure rate should be reviewed weekly. If >2% of turns hit fallback, investigate model stability.

---

## Item 5 — Risk Tier → Response Formatter Merge

### What It Is

The Risk Engine runs in parallel with Phase 1. When it assigns Tier 1 or 2, the patient response must include a clinic nudge — but currently this nudge only appears in Phase 2 responses. If Phase 1 decides `context_sufficient: false` and sends a clarifying question, no nudge is added. The patient gets a question with no escalation guidance.

**The fix:** The Response Formatter merges Phase 1 output + Risk Engine output before sending. Phase 1 stays focused on classification.

### Research Area 5a — Nudge Content per Tier

**✅ BUILT: `engine/response_formatter.py` → `TIER_NUDGE_TEXT`**

Tier 3 is split into three variants (3A/3B/3C) — not one generic message. Evidence basis:
different symptom types require genuinely different action sequences, not just language variation.
Chest pain at Tier 3 does not exist — it is always Tier 4 (bypasses this module entirely).

| Tier | Variant | Nudge text (English draft) | Placement | Status |
|------|---------|---------------------------|-----------|--------|
| 0 | — | Nothing | — | 🟢 Locked |
| 1 | — | "This is worth mentioning to your doctor at your next visit — within the next month or so." | After response | 🟢 Locked — clinical review deferred to production gate |
| 2 | — | "It is good to check this with a doctor in the next week or two — catching it early makes treatment easier. You do not need to wait for your next scheduled visit. Your nearest government health centre (PHC or FHC) can help." | After response | 🟢 Locked — clinical review deferred to production gate |
| 3A | foot_wound | "A wound or sore on the foot that is not healing needs to be seen by a doctor within the next day or two. For people with diabetes, even a small wound can become serious if not checked early. Please visit a clinic soon — take a family member with you if you can." | **Before** clarifying Q | 🟢 Locked — clinical review deferred to production gate |
| 3B | high_bg | "Blood sugar that stays this high for several days needs to be checked by a doctor within the next day or two. If you also feel like vomiting, are very thirsty, or feel very weak — go immediately, do not wait." | **Before** clarifying Q | 🟢 Locked — clinical review deferred to production gate |
| 3C | hypoglycemia | "If your sugar is low right now — eat something sweet immediately. A spoonful of sugar, a sweet drink, or glucose tablets. Then rest and check again in 15 minutes. If you still feel very weak or dizzy after that, call someone to take you to a clinic right away." | **Before** clarifying Q — always | 🟢 Locked — clinical review deferred to production gate |
| 3 | default | "This needs to be checked by a doctor within the next day or two. Please visit your nearest clinic — take a family member with you if you can." | **Before** clarifying Q | 🟢 Locked (fallback only) |
| 4 | — | Risk Engine handles directly — this module not called | — | Blocked on B4 |

**Design principles applied (research-grounded):**
- Positive-reason framing over threat framing — Kerala fatalism increases with threat messages (Voices of Care, PMC11155455)
- "PHC/FHC" over "hospital" — removes cost anxiety signal (38% of Kerala diabetes households face catastrophic expenditure, PMC 2024)
- Family framing in Tier 3 — transport dependency is a documented barrier (Voices of Care)
- Tier 3C self-action first — ADA 2026 15-15 rule; directing an actively hypoglycemic patient to "go to clinic" without immediate self-treatment is unsafe
- Tier 3B embeds within-message DKA escalation — avoids needing extra conversation turn

**7 items deferred to production gate (not build blockers):**

These no longer block Phase 1 code. Schedule with Dr. Rakesh before the base model goes live with real patients.

| # | Item | Why |
|---|------|-----|
| 1 | Tier 1: "within the next month or so" | Is one month the right soft window for Tier 1 signals? |
| 2 | Tier 2: PHC/FHC framing | Should Sugar Care Clinics be named in Tier 2 nudges? |
| 3 | Tier 3A: "even a small wound can become serious" | Confirm threshold statement for Kerala T2DM patients |
| 4 | Tier 3B DKA symptom list | "vomiting, very thirsty, very weak" — confirm patient-language equivalents |
| 5 | Tier 3C fast-carb examples | "spoonful of sugar, sweet drink, or glucose tablets" — confirm Kerala equivalents for 15g fast-acting carb |
| 6 | Tier 3C trigger condition | Currently symptomatic (→ 3C) vs historical low (→ Tier 2) — needs clinical + engineering alignment |
| 7 | Malayalam translations | All tier texts need native Malayalam speaker review with clinical vocabulary |

**Items engineering can lock without clinical review:**
- The 3-way Tier 3 split structure (A/B/C) — supported by ADA 2026 and plain language evidence
- `tier_3_subtype` field addition to Risk Engine output contract
- Placement rules (after for 1/2, before for 3)
- Family framing decision in Tier 3 (evidence-grounded)
- Not naming Sugar Care Clinics in Tier 1/2 (can be revisited)

---

### Research Area 5b — Integration Point in Pipeline

```python
# In the pipeline after Phase 1 and Risk Engine have both returned:

async def build_response(phase1_output: dict, risk_tier: int, user_message: str) -> dict:
    
    # Tier 4: bypass everything, return emergency response
    if risk_tier == 4:
        return build_emergency_response()
    
    # Phase 1 says context is sufficient → go to Phase 2
    if phase1_output["context_sufficient"]:
        phase2_response = await run_phase2(phase1_output, user_message)
        # Risk Engine nudge appended inside Phase 2's response formatter
        return add_risk_nudge(phase2_response, risk_tier)
    
    # Phase 1 says clarify first → format clarifying question + attach risk nudge
    else:
        clarifying_response = format_clarifying_question(phase1_output)
        return add_risk_nudge_to_clarification(clarifying_response, risk_tier)


def add_risk_nudge_to_clarification(clarifying: dict, tier: int) -> dict:
    if tier == 0:
        return clarifying   # no nudge needed
    
    nudge = TIER_NUDGE_TEXT[tier]
    
    if tier <= 2:
        # Nudge after question
        clarifying["text"] = f"{clarifying['text']}\n\n{nudge}"
    elif tier == 3:
        # Nudge before question (urgency first)
        clarifying["text"] = f"{nudge}\n\nAlso, to help you better — {clarifying['text']}"
    
    return clarifying
```

**Decisions locked:**
- `build_response()` returns a dict with `text`, `risk_tier`, `intent`, `qds_score`, `_clarifying` — frontend uses `risk_tier` for debug display
- `_clarifying_questions` list is passed separately in the response dict — frontend renders buttons/list from this, not from the text field
- `tier_3_subtype` is a new field the Risk Engine must pass when `risk_tier == 3`

**`tier_3_subtype` contract (Risk Engine output must include this):**
```python
# Risk Engine output when tier == 3:
{
    "risk_tier": 3,
    "tier_3_subtype": "foot_wound" | "high_bg" | "hypoglycemia"
    # Falls back to "default" nudge if missing or unrecognised
}
```
This is a small addition to the Risk Engine output contract. The Risk Engine already classifies the trigger signal (foot wound keyword, BG threshold, hypoglycemia threshold) — subtype is derived directly from that classification.

---

## Item 6 — Mark `base_model_spec.md` as Superseded

### What It Is

`base_model_spec.md` (v0.2, 2026-05-13) contains architectural decisions that have since been overridden. A new engineer or clinical reviewer reading it would get the wrong picture.

### Conflicts

| `base_model_spec.md` says | Current authoritative decision |
|--------------------------|-------------------------------|
| Vector DB: **Qdrant** (self-hosted Docker) | **pgvector on Neon** (managed PostgreSQL) |
| LLM: **`claude-sonnet-4-6`** | **Gemini 2.0 Flash** (Phase 1), **Gemini 2.5 Pro** (Phase 2) |
| No two-phase architecture | Full two-phase (Context Engine + RAG) |
| Reranker: top-3 to 5 output | **Top-5 locked** |
| No lead capture layer | Full lead capture layer — Section 12 of BOT_CONVERSATION_ARCHITECTURE.md |
| Memory compressor: not specified | **Gemini 2.0 Flash**, end-of-session cheap call |
| Reranker: "bge-reranker-large or Cohere Rerank" | **bge-reranker-large locked** (Cohere removed) |

### Action

✅ **Already done:** Added SUPERSEDED header to `base_model_spec.md` — see that file.

The file is retained (not deleted) because its design principles section (Section: Design Principles) and component breakdown are still accurate at the concept level. Only the technology choices are wrong.

---

## Build Order Summary

```
Phase 1 code can only begin after Items 1–4 are complete.
Item 5 (risk merge) can be done in parallel with Phase 1 code — it's in the Response Formatter.
Item 6 is a 30-min action (done).

ITEM 1 — System Prompt           🟢 DONE — prompts/phase1_system_prompt.txt locked; Dr. Rakesh validation deferred to D7
ITEM 2 — profile_signals schema  🟢 DONE — schemas/phase1_schema.py + schemas/phase1_output_schema.json
ITEM 3 — Mid-clarification state 🟢 DONE — Option C prompt rule + engine/query_builder.py built
ITEM 4 — Error fallback          🟢 DONE — engine/phase1_runner.py + 37/37 unit tests passing
ITEM 5 — Risk tier merge         🟢 DONE — engine/response_formatter.py locked; 7 clinical items deferred to production gate
ITEM 6 — base_model_spec.md      🟢 DONE
```

---

## Dependencies Map

```
Item 1 (system prompt)
    → required before: Phase 1 code (ingestion/phase1/ or engine/phase1.py)
    → Item 3 mid-clarification rule belongs inside Item 1 prompt (Rule 1e)
    → needs: clinical input on Kerala patient language examples (1b)
    → needs: intent enum decision (1d) — is intent used by Phase 2 filter?

Item 2 (profile_signals schema)
    → required before: Neon DB Step 2 schema (users table final columns)
    → required before: Phase 1 signal writer function
    → required before: Phase 2 query enrichment (build_enriched_query reads signals)
    → needs: clinical input on medication vocabulary (2b)

Item 3 (mid-clarification state)
    → required before: Phase 1 code
    → Option C (conversation context detection) — rule in prompt edge_cases block ✅
    → Phase 2 query construction (3b merged query) — built in engine/query_builder.py ✅
    → build_phase2_query() handles all 3 paths: new user / returning user / mid-clarification

Item 4 (error fallback)
    → required before: Phase 1 code ships
    → no dependencies on other items
    → PHASE1_FALLBACK defaults should be agreed with clinical lead (qds_score=2 assumption)

Item 5 (risk tier merge)
    → required before: Response Formatter code (Step 6 in build order)
    → nudge text strings need Dr. Rakesh review before hardcoding
    → Tier 3 nudge split decision: single nudge vs symptom-specific nudges?

Item 6 (base_model_spec.md)
    → ✅ DONE — no further action needed
```

---

## Production Gate Items for Dr. Rakesh (not build blockers)

These no longer block Phase 1 code. Schedule as a pre-launch clinical review session before the base model goes live with real patients (D7 gate in BOT_CONVERSATION_ARCHITECTURE.md Section 14):

1. **QDS validation — 50 real patient questions:** Run through the prompt, check classification accuracy, iterate on any misclassifications.

2. **QDS 5 definition:** Is "I'm scared" alone enough for QDS 5 (distressed)? Or does it need a clinical fear component ("scared about insulin starting")?

3. **Kerala brand name confirmation:** Glycomet, Amaryl, Januvia/Galvus, Farxiga/Jardiance, Ozempic/Victoza, Lantus/Basaglar, Novomix/Mixtard — confirm these are the brand names most commonly dispensed to Kerala T2DM patients.

4. **Tier 1/2 nudge language:** Confirm wording is clinically appropriate and non-alarmist. Confirm PHC/FHC framing vs Sugar Care Clinics.

5. **Tier 3 nudge content:** Confirm DKA symptom list (3B), fast-carb examples for Kerala (3C), and threshold statement for foot wound (3A).

6. **Complication vocabulary:** Any Kerala-specific symptom descriptions for neuropathy, retinopathy, nephropathy patients commonly use that aren't in the prompt yet?

7. **Malayalam translation review:** All patient-facing tier texts need a native Malayalam speaker with clinical vocabulary — do not auto-translate at runtime.

---

*Update this document as items are completed. When all items reach 🟢, Phase 1 code (Build Order Step 4 in BOT_CONVERSATION_ARCHITECTURE.md) can begin.*
