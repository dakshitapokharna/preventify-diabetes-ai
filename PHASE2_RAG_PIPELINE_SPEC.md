# Phase 2 — RAG Pipeline: Build Specification

**Version:** 1.0  
**Date:** 2026-05-24  
**Status:** All 10 design items 🟢 DONE — Phase 2 code complete, tests complete  
**Authoritative architecture:** `BOT_CONVERSATION_ARCHITECTURE.md` Section 7  
**Phase 1 equivalent:** `PHASE1_CONTEXT_ENGINE_SPEC.md`

---

## What This Document Is

`BOT_CONVERSATION_ARCHITECTURE.md` defines *what* Phase 2 does and *why*.  
`engine/phase2_runner.py` implements the 15-step RAG pipeline.  
This document resolves every remaining *assumption* — design decisions not yet locked in either of those files.

Before this document, Phase 2 had working code but 10 unresolved design items that would create ambiguity during testing and clinical review. Each item is resolved here with a locked decision, the rationale, and the code change that implements it.

---

## Design Items — Status at Close of This Session

| # | Item | Status | Output |
|---|------|--------|--------|
| 1 | Phase 2 system prompt audit — gaps identified and filled | 🟢 Done | `prompts/phase2_system_prompt.txt` (updated) |
| 2 | Literacy register switching (low/mid/high) | 🟢 Done | `<literacy_register_guide>` block added to prompt |
| 3 | QDS-aware response depth calibration | 🟢 Done | `<qds_response_guide>` block added to prompt |
| 4 | Intent routing in Phase 2 | 🟢 Done | `<intent_routing>` block added to prompt |
| 5 | session_context (family_member_inquiry) pass-through | 🟢 Done | `<phase1_context>` injected into Gemini contents |
| 6 | Phase 1 context (intent, QDS, session_context) injection | 🟢 Done | `engine/phase2_runner.py` updated |
| 7 | no_chunks_retrieved fallback behavior fix | 🟢 Done | `engine/phase2_runner.py` updated |
| 8 | SaMD conversation audit log | 🟢 Done | `schemas/conversation_audit_log.sql` new |
| 9 | Phase 2 unit tests | 🟢 Done | `tests/engine/test_phase2_schema.py` (38 tests) |
| 10 | Age-aware response (elderly protocol — ADA S12) | 🟢 Done | `<response_format>` block updated in prompt |

---

## Item 1 — Phase 2 System Prompt Audit

### Pre-audit state

`prompts/phase2_system_prompt.txt` had 6 sections:
- `<role>` — DSMES educator, not a doctor
- `<safety_rules>` — 5 hard rules with examples
- `<chunk_usage_rules>` — grounding, India-first preference, safety caution chunks
- `<response_format>` — length, language, Kerala specifics, QDS 5 emotional protocol
- `<escalation_protocol>` — emergency signals + response templates

### Gaps identified

| Gap | What was missing | Impact |
|-----|-----------------|--------|
| No literacy register guidance | Bot had no way to adjust language to patient education level | All responses written at the same register — wrong for 30% of patients |
| No QDS 1–4 response calibration | QDS 5 had a protocol but QDS 1–4 didn't | QDS 1 responses as long as QDS 4; QDS 4 not substantive enough |
| No intent routing | 9 intents from Phase 1 never reached Phase 2 | `drug_education` turns not extra-careful; `fasting_protocol` not referencing IDF-DAR thresholds |
| No family_member_inquiry handling | session_context signal not passed to Phase 2 | Family supporters got "you have diabetes" framing |
| No age-aware guidance | No elderly protocol despite ADA S12 being in corpus | 65+ patients got same advice as 30-year-olds |
| intent/qds_score not injected into Gemini | Phase 2 had no way to read these values | All guidance above was inapplicable even if added to prompt |

### Resolution

Added 4 new blocks to `phase2_system_prompt.txt`:
- `<literacy_register_guide>` (Item 2)
- `<qds_response_guide>` (Item 3)
- `<intent_routing>` (Item 4 + Item 5)
- Age-aware extension to `<response_format>` (Item 10)

Added injection of `<phase1_context>` block to Gemini contents (Item 6) — this is the bridge that makes all prompt guidance actionable.

**Token count after updates:** ~2,150 tokens (well above 1,024 cache minimum; below 2,500 so within cost budget).

---

## Item 2 — Literacy Register Switching

### Clinical rationale

Kerala T2DM patients span extreme literacy ranges:
- Rural agricultural workers, elderly patients (70%+ of Sugar Care Clinic pipeline): Malayalam-medium education, no clinical vocabulary, never heard "HbA1c"
- Urban educated professionals, returning Gulf migrants: comfortable with English, have researched their condition online
- A bot that speaks at one register serves no one well

### Three registers defined

**LOW LITERACY:**  
Detection: very short messages ("sugar high"), spelling variations, Malayalam/English switching without clinical terms.  
Action: No medical jargon at all. "HbA1c" → "the 3-month sugar test." "Hyperglycemia" → "when sugar goes too high." Concrete Kerala examples only (rice ladle, chaaya cup). Maximum 2 short sentences per point.

**MID LITERACY (default):**  
Detection: Uses English but not clinical terms. Knows "diabetes tablet" but not "metformin." Most patients fall here.  
Action: Plain English. Medical term always followed by a plain-language explanation in the same sentence. 3–4 short paragraphs.

**HIGH LITERACY:**  
Detection: Uses clinical terms correctly without prompting ("my creatinine is elevated," "SGLT2 inhibitor").  
Action: Clinical terms acceptable. Cite guideline type ("current guidelines recommend") not specific names. More nuance and detail appropriate.

**Default rule:** Start at MID. Switch UP only when the patient uses correct clinical terminology unprompted. Switch DOWN when confusion signals appear ("what does that mean?", "I don't understand").

### Implementation note

Literacy register detection is done by Phase 2 Gemini from the current message + session history — no separate classification step. The `<literacy_register_guide>` block gives Gemini the detection rules and action rules.

✅ **Item 2 locked. Added to `prompts/phase2_system_prompt.txt`.**

---

## Item 3 — QDS-Aware Response Depth

### Why QDS matters in Phase 2

Phase 1 assigns a QDS score (1–5). Without guidance, Gemini writes at the same depth for every query. A QDS 1 ("What is HbA1c?") should be 2–3 short sentences. A QDS 5 ("Starting insulin and I'm scared") should start with emotional acknowledgment, not clinical facts.

### Response depth per QDS score

| QDS | Patient intent | Response length | Key rule |
|-----|---------------|----------------|---------|
| 1 | General awareness — "What is HbA1c?" | 2–3 sentences | Define clearly. One concrete example. Nothing more. |
| 2 | Personal relevance — "My HbA1c is 7.8" | 3–4 sentences | Acknowledge their specific value. One benchmark. One actionable step. |
| 3 | Active management — "Should I take tablet before or after food?" | 4–5 sentences | Address the decision directly. Be clear what's in educator scope vs. what needs doctor. One concrete action. |
| 4 | Complication concern — "My feet go numb" | 4–6 sentences | Acknowledge the concern. Explain what it might mean (no diagnosis). What to monitor. What to tell the doctor. Escalation guidance. |
| 5 | Distressed / complex — "Starting insulin, I'm scared" | Variable | Acknowledge emotion FIRST. One key reassurance. One concrete next step. Warm closing. Never overwhelming. |

**Critical rule: QDS 5 is never the first response to a question.** If Phase 1 classified QDS 5, it means `context_sufficient=True` was already set — proceed straight to Phase 2, answer immediately. Never ask clarifying questions for distressed patients.

### Implementation

`<phase1_context>` block carries `QDS {score}` and Phase 2 applies the correct depth from `<qds_response_guide>`.

✅ **Item 3 locked. Added to `prompts/phase2_system_prompt.txt`.**

---

## Item 4 — Intent Routing in Phase 2

### The gap

Phase 1 outputs `intent` (9 values). `BOT_CONVERSATION_ARCHITECTURE.md` Section 7.3 confirmed the metadata pre-filter does NOT use `intent` for pgvector filtering (it uses `retrieval_tier` and `condition_trigger`). But `intent` is still valuable for Phase 2 response behavior. This was the open gap identified in PHASE1_CONTEXT_ENGINE_SPEC.md Item 1d.

### Resolution: intent guides response style, not retrieval

| Intent | Phase 2 behavior |
|--------|-----------------|
| `drug_education` | Extra constraint vigilance. Explain mechanism, side effects, storage freely. For any dose question: "Your doctor sets your dose — what I can tell you is how the medicine works." For missed dose: "When in doubt, call your clinic — do not double up." |
| `fasting_protocol` | Acknowledge religious significance first. For Ramadan: can explain suhoor/iftar medication timing (educator scope). SMBG thresholds to break fast (BG <70 or >300 per IDF-DAR) can be stated but always add "your doctor should confirm this for you." |
| `nutrition_education` | Kerala food specifics apply. Use ladle measures for rice. Always address chaaya when diet is the topic. ICMR-NIN values are authoritative for carb counts. |
| `complication_screening` | Acknowledge concern without diagnosing. "This is worth a doctor's exam — even if it turns out to be nothing." Give what to observe and report. |
| `lifestyle_education` | Exercise, weight, stress, sleep. ADA exercise guidance can be cited. For Kerala context: monsoon indoor alternatives, walking after meals. |
| `monitoring` | SMBG frequency, CGM basics, HbA1c interpretation within educator scope. Refer to RSSDI and ADA S7. |
| `symptom_query` | Symptom → possible cause (educate) → see a doctor. Never diagnose from symptoms. |
| `escalation_only` | Should never reach Phase 2. Risk Engine intercepts. If somehow present: respond only with emergency escalation text. |
| `general_dsmes` | No special rules — general diabetes education. |

✅ **Item 4 locked. Added to `prompts/phase2_system_prompt.txt` in `<intent_routing>` block.**

---

## Item 5 — session_context (family_member_inquiry) Pass-Through

### The gap

Phase 1 extracts `session_context: "family_member_inquiry"` when a patient is asking about a family member's diabetes, not their own. This signal is explicitly marked "NOT written to DB" in signal_writer.py — it's session memory only. But Phase 2 never received it, so family supporter queries got "you have diabetes" framing.

### Resolution

`session_context` is passed from `phase1_output["profile_signals"]["session_context"]` into the `<phase1_context>` block in Gemini contents.

**When `session_context == "family_member_inquiry"`:**
- Frame response to support a family caregiver: "To help your family member managing diabetes..."
- Full clinical accuracy still required — the answer just changes framing
- If family member is remote / Gulf: note telemedicine options and remote monitoring tools

**When `session_context == "self"` (default):**
- Standard patient-directed framing

✅ **Item 5 locked. Added to `<intent_routing>` block and injected via `<phase1_context>` in `run_phase2()`.**

---

## Item 6 — Phase 1 Context Injection into Gemini

### The gap

`phase1_output` contains `intent`, `qds_score`, and `profile_signals.session_context`. These are valuable for calibrating the Phase 2 response, but `_build_gemini_contents()` never received them. All guidance in Items 2–5 was useless until this injection point was built.

### Implementation

**`_build_gemini_contents()` new signature:**
```python
def _build_gemini_contents(
    current_message: str,
    session_turns: list,
    short_memory: str,
    chunk_context_block: str,
    intent: Optional[str] = None,
    qds_score: Optional[int] = None,
    session_context: Optional[str] = None,
) -> list:
```

**Injected `<phase1_context>` block (prepended before patient_memory):**
```
<phase1_context>
Question intent: drug_education
Question depth: QDS 3
Conversation context: Patient is asking about a family member's diabetes, not their own condition.
</phase1_context>
```

Only non-default values are included:
- `intent` is included unless it is `general_dsmes` (default — no special behavior needed)
- `qds_score` is always included
- `session_context` is included only when `"family_member_inquiry"` — default `"self"` is omitted

**`run_phase2()` extracts and passes these:**
```python
intent          = phase1_output.get("intent")
qds_score       = phase1_output.get("qds_score")
session_context = phase1_output.get("profile_signals", {}).get("session_context")
```

### Contents ordering (final)

```
Turn 1 (user):
  <phase1_context>...</phase1_context>    ← NEW: intent, QDS, family context
  <patient_memory>...</patient_memory>    ← unchanged
  <clinical_context>...</clinical_context> ← unchanged

Turn 2 (model — acknowledgment placeholder):
  "Understood. I have read the patient's profile and the clinical evidence. Please share the conversation."

Turn 3+ (alternating patient/model — session history):
  ...last 5 turns...

Final turn (user):
  <current patient message>
```

✅ **Item 6 locked. `engine/phase2_runner.py` updated.**

---

## Item 7 — no_chunks_retrieved Behavior Fix

### The problem

**Current behavior (wrong):**
```python
if not top_20_chunks:
    _log_failure(current_message, user_id, "no_chunks_retrieved", "", 1)
    return _make_fallback("no_chunks_retrieved")
```
Patient receives: *"I'm sorry — I'm having a little trouble finding the right information for you right now."*

**Why this is wrong:**
For a QDS 1 question ("What is HbA1c?"), `format_chunks_for_prompt([])` already returns:
```
<clinical_context>
No clinical evidence was retrieved for this query.
Answer only from general DSMES educator knowledge. If uncertain, say so.
</clinical_context>
```

Gemini 2.5 Pro knows that HbA1c means a 3-month average blood glucose — this does not require a clinical chunk to answer correctly. The `no_chunks_retrieved` fallback was unnecessarily blocking general-knowledge answers.

**When would no chunks be retrieved?**
1. Embedder has not been run yet (database is empty) — correct behavior is still to try from general knowledge
2. Query is so unusual it matches nothing (rare) — still better to try
3. Metadata filter too restrictive (very unusual condition flag combination) — try from general knowledge

**Correct behavior:**
Log as a warning (ops signal — should be rare once embedder is populated), skip reranking, pass empty chunks to `format_chunks_for_prompt([])`, and proceed with generation.

### Fixed code

```python
if not top_20_chunks:
    log.warning(
        "phase2_runner: no chunks retrieved for query=%r — proceeding with "
        "general DSMES knowledge only",
        enriched_query[:80],
    )
    # Log as ops signal (should be rare once embedder is populated).
    # Do NOT return fallback — format_chunks_for_prompt([]) handles empty chunks
    # correctly by telling Gemini to answer from general educator knowledge.
    _log_failure(
        current_message, user_id, "no_chunks_retrieved_general_only", "", 0
    )
    # Skip reranking — proceed directly to chunk formatting
    top_5_chunks = []
    top_5_scores = []
    query_cache_hit = False
```

Then the `if not top_20_chunks:` block falls through to `format_chunks_for_prompt(top_5_chunks)` naturally.

**One remaining true fallback case:** If reranking itself fails AND no chunks exist, the degraded path (ANN order) also produces empty chunks. This is handled correctly by the empty-chunk path above.

✅ **Item 7 locked. `engine/phase2_runner.py` updated.**

---

## Item 8 — SaMD Conversation Audit Log

### Why this is required

The system is anticipated as SaMD Class B under CDSCO. Clinical content generation must be auditable. The `chunks_used` list in the Phase 2 return dict provides the audit trail, but it disappears when the API response is sent unless explicitly stored.

**Minimum required for SaMD audit:**
- What the patient asked
- What evidence was retrieved (chunk_ids)
- What the bot responded
- Whether constraints were triggered
- Risk tier assigned
- Timestamp (IST)
- Patient identifier

### Table design

**`schemas/conversation_audit_log.sql`** — append-only, never update or delete rows.

```sql
CREATE TABLE IF NOT EXISTS conversation_audit_log (
    id                      SERIAL PRIMARY KEY,
    user_id                 TEXT NOT NULL,
    session_id              TEXT NOT NULL,
    message_id              TEXT NOT NULL UNIQUE,  -- UUID per turn
    turn_number             INT,
    patient_message         TEXT NOT NULL,
    bot_response            TEXT,                  -- NULL for clarifying-question turns
    response_type           TEXT NOT NULL,         -- 'phase2_response' | 'clarifying_question' | 'risk_escalation' | 'fallback'
    qds_score               INT,
    risk_tier               INT,
    intent                  TEXT,
    chunks_used             TEXT[]  NOT NULL DEFAULT '{}',
    condition_flags_active  TEXT[]  NOT NULL DEFAULT '{}',
    query_cache_hit         BOOLEAN,
    reranker_scores         FLOAT[] NOT NULL DEFAULT '{}',
    constraint_violation    BOOLEAN NOT NULL DEFAULT FALSE,
    constraint_violations   TEXT[]  NOT NULL DEFAULT '{}',
    phase1_fallback         BOOLEAN NOT NULL DEFAULT FALSE,
    phase2_fallback         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_user_session
    ON conversation_audit_log (user_id, session_id);
CREATE INDEX IF NOT EXISTS idx_audit_created_at
    ON conversation_audit_log (created_at);
CREATE INDEX IF NOT EXISTS idx_audit_risk_tier
    ON conversation_audit_log (risk_tier);
CREATE INDEX IF NOT EXISTS idx_audit_constraint_violation
    ON conversation_audit_log (constraint_violation)
    WHERE constraint_violation = TRUE;
```

**DPDP retention policy:**
- Retention period: 3 years from message date (SaMD audit requirement; align with DPDP Act 2023 principles of purpose limitation)
- Access controls: restricted to clinical reviewers and compliance team
- Anonymization: before retention expiry, `patient_message` and `bot_response` text columns may be replaced with hashes for de-identification — decision pending B5 (compliance sign-off)

**Who writes to this table:**
The pipeline orchestrator (future `engine/pipeline.py` or the API handler) writes to `conversation_audit_log` after every turn completes — using the `chunks_used`, `condition_flags_active`, `reranker_scores`, and `_fallback` fields from the Phase 2 return dict, combined with Phase 1 and Risk Engine outputs.

Phase 2 runner itself does NOT write to the audit log — it returns the data needed to populate it.

✅ **Item 8 locked. `schemas/conversation_audit_log.sql` created.**

---

## Item 9 — Phase 2 Unit Tests

### What to test

Phase 2 has four pure functions in `schemas/phase2_schema.py` that are fully testable without any API calls or DB connections:
1. `resolve_condition_flags(message, stored_flags)` → set of active flags
2. `build_retrieval_filter(flags)` → (tier_filter, trigger_filter)
3. `check_constraints(text)` → (ok, violations)
4. `format_chunks_for_prompt(chunks)` → XML string

**File:** `tests/engine/test_phase2_schema.py` — 38 tests total

### Test plan per function

**`resolve_condition_flags` — 12 tests:**
- New user, no message signals → empty set
- CKD keyword in message ("my creatinine is high") → {"ckd"}
- Cardio keyword ("heart problem") → {"cardio"}
- Ramadan keyword ("roza") → {"ramadan"}
- Hypertension keyword ("bp high") → {"hypertension"}
- Multiple flags from one message ("heart attack and kidney issue") → {"cardio", "ckd"}
- Stored flag persists even with neutral message ("can I eat rice?", stored=["ckd"]) → {"ckd"}
- Stored + new message flag → union
- Case insensitivity ("BP HIGH", "RAMADAN") → correct flags
- Profile None with no message signal → empty set
- Profile with unknown flag string → only valid flags in result
- Empty message, stored flags → stored flags returned

**`build_retrieval_filter` — 6 tests:**
- Empty flags → ("core", None)
- Single flag {"ckd"} → (["core", "triggered"], ["ckd"])
- Multiple flags {"ckd", "cardio"} → (["core", "triggered"], ["ckd", "cardio"])
- All four flags → (["core", "triggered"], [...all four...])
- trigger_filter is a list not set (consistent type)
- tier_filter is a list not a string when flags present

**`check_constraints` — 14 tests:**
- Clean response (no violations) → (True, [])
- Response with "500 mg" → (False, ["specific_dose_mg"])
- Response with "10 units" → (False, ["specific_dose_units"])
- Response with "20 IU" → (False, ["specific_dose_IU"])
- "stop your tablet" → (False, ["stop_medication"])
- "discontinue your medication" → (False, ["stop_medication"])
- "reduce your dose" → (False, ["reduce_dose"])
- "take less insulin" → (False, ["reduce_dose"])
- "cut your dose in half" → (False, ["reduce_dose"])
- "you should skip your dose" → (False, ["skip_dose"])
- "you have diabetes" → (False, ["diagnosis"])
- "you are diabetic" → (False, ["diagnosis"])
- "your creatinine means you have CKD" → (False, ["lab_conclusion"])
- Multiple violations in one text → all listed

**`format_chunks_for_prompt` — 6 tests:**
- Empty chunks → returns "No clinical evidence" block
- Single chunk → correct header + text
- 5 chunks → all numbered [1]–[5]
- safety_critical chunk → "[SAFETY CAUTION — DO NOT RECOMMEND]" label present
- grade_priority=5 chunk → grade shown correctly
- Source name and year rendered correctly

✅ **Item 9 locked. `tests/engine/test_phase2_schema.py` created with 38 tests.**

---

## Item 10 — Age-Aware Response (Elderly Protocol)

### Clinical rationale

ADA 2026 Section S12 defines distinct management targets for older adults with diabetes:
- HbA1c target: 7.5–8.5% acceptable (vs 7.0% for non-elderly) depending on health complexity
- Fall risk: certain medications increase hypoglycemia and fall risk
- Hypoglycemia risk is higher in elderly → more caution on meal timing, skipping meals, alcohol
- Cognitive load: shorter, more explicit step-by-step instructions needed

### Implementation

The `<patient_memory>` block already carries age when known. Gemini needs instructions to use it.

**Added to `<response_format>` in system prompt:**

```
Age-aware guidance:
  If <patient_memory> shows age ≥ 65:
  → HbA1c targets: mention that the target may be slightly higher for older adults — their doctor sets the right target for them specifically. Do not quote a number.
  → Fall risk: when discussing exercise, briefly mention balance and safety.
  → Hypoglycemia: extra emphasis on caution. "Missing a meal while on diabetes tablets or insulin is more risky as you get older — please eat regularly."
  → Response length: slightly shorter. More explicit step-by-step instructions. Less medical detail at once.
```

**Why not set a specific HbA1c number in the prompt:**
The system prompt should not contain specific dose or target numbers — this is consistent with the constraint rules. Saying "your target may be slightly higher" without a specific number is within educator scope and avoids the constraint patterns.

✅ **Item 10 locked. Added to `<response_format>` in `prompts/phase2_system_prompt.txt`.**

---

## Constraint Checker — Known Limitation

### False positive risk: food quantity mg values

Pattern `\b\d+\s*mg\b` catches drug doses (correct) but also food nutritional values like "spinach has 80mg of calcium per 100g" (false positive). 

**Assessment:** In practice this false positive is rare in DSMES responses. A diabetes educator explaining food composition would say "this food is rich in potassium" rather than "this food has 400mg of potassium." The ICMR-NIN chunks do contain mg values for micronutrients, but Gemini 2.5 Pro does not typically reproduce raw nutritional table values verbatim.

**Monitoring:** Review `logs/phase2_failures.jsonl` for `constraint_violation` entries weekly during testing. If false positive rate > 5% of constraint violations, refine the pattern.

**Future refinement (if needed):**
```python
# More specific: only flag if mg follows drug-related context within 10 chars
(r"(?:tablet|dose|medicine|metformin|insulin|inject)\w{0,15}\s+\d+\s*mg\b", "specific_dose_mg"),
```

This refinement is deferred until false positives are observed in practice.

---

## Phase 2 Full Pipeline Reference (Post-Spec)

The 15-step pipeline in `engine/phase2_runner.py`, with all design decisions resolved:

```
run_phase2(current_message, session_turns, phase1_output, profile, short_memory,
           db_conn, embedder, reranker, user_id)
    │
    ├─ Guard 1: GEMINI_API_KEY not set → PHASE2_FALLBACK
    ├─ Guard 2: intent == "escalation_only" → escalation_bypass (not an error)
    │
    ├─ Step 1: build_phase2_query()
    │   ├─ Path A: new user → current_message (no enrichment)
    │   ├─ Path B: returning user → "message [Patient context: T2DM; ckd; on metformin]"
    │   └─ Path C: mid-clarification → "original Q — clarification: answer [Patient context: ...]"
    │
    ├─ Step 2: resolve_condition_flags(current_message, profile.condition_flags + phase1_flags)
    │   → set of {"ckd", "cardio", "ramadan", "hypertension"}
    │
    ├─ Step 3: build_retrieval_filter(active_flags)
    │   → ("core", None)  OR  (["core", "triggered"], [flag_list])
    │
    ├─ Step 4: _check_query_cache(db_conn, query_hash)
    │   └─ HIT: fetch chunks by chunk_id list → skip to Step 8
    │   └─ MISS: proceed to embedding
    │
    ├─ Step 5: _embed_async(embedder, enriched_query)
    │   → 1024-dim float32 numpy array
    │   └─ FAIL: return PHASE2_FALLBACK (embed_error)
    │
    ├─ Step 6: _pgvector_search(conn, embedding, tier_filter, trigger_filter, top_k=20)
    │   → list of top-20 chunk dicts
    │   └─ FAIL: return PHASE2_FALLBACK (pgvector_error)
    │
    ├─ Step 7: _write_query_cache() — best-effort, non-blocking
    │
    ├─ Step 8: handle no chunks
    │   └─ EMPTY: log warning, set top_5_chunks=[], skip reranking [FIXED in Item 7]
    │
    ├─ Step 9: _rerank_async(reranker, enriched_query, top_20_chunks)
    │   → scores[0..1] per chunk
    │   └─ FAIL: degraded mode — use ANN order (synthetic equal scores), log warning
    │
    ├─ Step 10: Sort scored chunks, take top-5; then sort top-5 by grade_priority ASC
    │
    ├─ Step 11: format_chunks_for_prompt(top_5_chunks)
    │   → <clinical_context> block
    │   → empty case: "Answer only from general DSMES educator knowledge"
    │
    ├─ Step 12: _build_gemini_contents(current_message, session_turns, short_memory,
    │           chunk_context_block, intent, qds_score, session_context) [UPDATED in Item 6]
    │   → [
    │       Turn 1 user: <phase1_context> + <patient_memory> + <clinical_context>
    │       Turn 2 model: acknowledgment
    │       Turn 3+: session history (last 5 turns)
    │       Final: current_message
    │     ]
    │
    ├─ Step 13: _get_or_create_cache() → cache_name or None
    │
    ├─ Step 14: client.aio.models.generate_content(MODEL_ID, contents, config)
    │   → response.text
    │   └─ TIMEOUT: retry once, then PHASE2_FALLBACK
    │   └─ 429: retry once, then PHASE2_FALLBACK
    │   └─ 503: PHASE2_FALLBACK immediately
    │   └─ safety filter block: PHASE2_FALLBACK
    │
    ├─ Step 15: check_constraints(raw_text)
    │   └─ VIOLATION: log + return PHASE2_CONSTRAINT_FALLBACK_TEXT
    │
    └─ Return {text, chunks_used, condition_flags_active, query_cache_hit,
               reranker_scores, _fallback, _fallback_reason}
```

---

## Open Items (Not Build Blockers for Phase 2)

These are deferred to production gate or later build steps:

| # | Item | Reason for deferral |
|---|------|-------------------|
| P1 | Dr. Rakesh QDS response depth validation — do the 5 depth tiers feel right for real Kerala patients? | Same gate as D7 (QDS classification) |
| P2 | Literacy register detection accuracy — does Gemini correctly classify low/mid/high from patient messages? | Measure during testing phase |
| P3 | Constraint checker false positive rate monitoring | Review phase2_failures.jsonl during testing |
| P4 | Conversation audit log writer in pipeline orchestrator | Deferred to pipeline orchestrator build (after phase1.py is built) |
| P5 | Short memory compressor (`engine/memory_compressor.py`) — Gemini Flash end-of-session compression | Deferred to Build Order Step 8 |
| P6 | `engine/risk_engine.py` — deterministic red-flag scanner | Build Order Step 3 |
| P7 | `engine/session_manager.py` — session load, profile load, turn management | Build Order Step 2 |
| P8 | Lead capture + consent moment logic | Build Order Step 7 and 9 |
| P9 | Malayalam translation layer slots in around Phase 2 — no changes to Phase 2 code | Build Order Step 10 |

---

## Dependencies Map

```
Phase 2 code complete ─────────────────────────────────────────── DONE
    └─ Requires: GEMINI_API_KEY in .env                          (EXTERNAL)
    └─ Requires: bge-large-en-v1.5 in HF_HOME cache             (DOWNLOAD: pip run)
    └─ Requires: bge-reranker-large in HF_HOME cache             (DOWNLOAD: pip run)
    └─ Requires: preventify_corpus populated (embedder run)      (RUN: python ingestion/embedder/run.py)

Phase 1 orchestrator (engine/phase1.py) ──────────────────────── NEXT IMMEDIATE STEP
    └─ Calls run_phase1() → write_profile_signals() → build_phase2_query() → run_phase2()

Risk engine (engine/risk_engine.py) ─────────────────────────── Build Order Step 3
    └─ Provides: risk_tier, tier_3_subtype per turn

Session manager (engine/session_manager.py) ─────────────────── Build Order Step 2
    └─ Provides: user profile, session_turns, short_memory per turn

Response formatter (engine/response_formatter.py) ───────────── COMPLETE
    └─ Merges Phase 2 response + Risk Engine nudge

Conversation audit log write ────────────────────────────────── Pipeline orchestrator
    └─ Written by pipeline.py or API handler using Phase 2 return dict
```

---

## Code Changes Summary (this session)

| File | Change type | Description |
|------|------------|-------------|
| `prompts/phase2_system_prompt.txt` | Updated | Added `<literacy_register_guide>`, `<qds_response_guide>`, `<intent_routing>`, age-aware extension to `<response_format>` |
| `engine/phase2_runner.py` | Updated | `_build_gemini_contents()` accepts intent/qds_score/session_context; injects `<phase1_context>` block; fixed no_chunks_retrieved behavior |
| `schemas/conversation_audit_log.sql` | New | SaMD audit log table DDL |
| `tests/engine/test_phase2_schema.py` | New | 38 unit tests for Phase 2 schema functions |

---

## Testing Gate Items for Dr. Rakesh (production gate — not build blockers)

Before the bot goes live with real patients, validate:

1. **QDS response depth calibration** — run 20 sample questions (4 per QDS level) through Phase 2, review if response length and depth matches the QDS guidance. Expected: QDS 1 responses are 2–3 sentences, QDS 5 starts with emotional acknowledgment.

2. **Literacy register accuracy** — run 10 patient messages with clear low/mid/high register markers, check if Gemini selects the correct register.

3. **Intent routing validation** — run 5 `drug_education` queries, confirm extra constraint vigilance fires; run 3 `fasting_protocol` queries, confirm IDF-DAR threshold mentioned.

4. **Family member framing** — run 3 queries where Phase 1 sets `session_context: "family_member_inquiry"`, confirm Phase 2 frames response to the family supporter.

5. **Age-aware response** — run 3 queries with age > 65 in `short_memory`, confirm elderly HbA1c and hypoglycemia cautions are mentioned.

6. **Constraint checker** — run 5 drug-related queries, confirm no dose numbers appear in any response. Run 3 queries asking for diagnosis, confirm redirected correctly.

---

*End of document. Version 1.0 — 2026-05-24.*
*Update this document when any item changes status. Cross-reference with PHASE1_CONTEXT_ENGINE_SPEC.md and BOT_CONVERSATION_ARCHITECTURE.md.*
