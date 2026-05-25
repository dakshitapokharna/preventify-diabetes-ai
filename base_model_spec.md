# Base Model Specification — Preventify Diabetes Educator AI

> ⚠️ **SUPERSEDED — 2026-05-23**  
> This document (v0.2) reflects earlier architectural decisions. Several core choices have changed:  
> - Vector DB: ~~Qdrant (self-hosted)~~ → **pgvector on Neon (PostgreSQL)**  
> - LLM: ~~`claude-sonnet-4-6`~~ → **Gemini 2.0 Flash (Phase 1) + Gemini 2.5 Pro (Phase 2)**  
> - Architecture: ~~single-phase~~ → **two-phase Context Engine + RAG**  
> - Reranker output: ~~top-3 to 5~~ → **top-5 locked**  
>
> **Authoritative documents:**  
> - Full architecture: `BOT_CONVERSATION_ARCHITECTURE.md`  
> - Phase 1 build spec: `PHASE1_CONTEXT_ENGINE_SPEC.md`  
> - Stack decisions: `CLAUDE.md` → B1  
>
> This file is kept for its **Design Principles** section and component-level reasoning, which remain valid. Do not use it as a build reference.

---

**Version:** 0.2  
**Date:** 2026-05-13  
**Scope:** English-only query/response pipeline — the foundation layer before Malayalam/voice features are added.

---

## What "Base Model" Means Here

The base model is the complete English-language pipeline: a user submits a query in English and receives a clinically grounded response in English. It covers every stage from query intake to response generation. Malayalam ASR, voice I/O, and code-mixed language handling are **not** in scope here — those are layered on top once this pipeline is validated.

---

## High-Level Pipelin
```
User Query (English text)
        │
        ▼
 ┌─────────────────┐
 │   Query Parser  │  — intent classification, topic tagging, patient context extraction
 └────────┬────────┘
          │
          ▼
 ┌─────────────────────────────────────────────────────┐
 │              Parallel: Risk Engine                  │  — runs on EVERY turn, silently
 │   Patient state (Postgres) → Rule engine            │  — Tier 0–4 escalation decision
 │   Hard-coded red-flag triggers → escalation action  │  — never touches vector DB
 └────────────────────────────┬────────────────────────┘
          │                   │
          │           [Tier 3 / 4 detected]
          │                   │
          │                   ▼
          │         Escalation Response Path
          │         (bypass RAG, return safety instructions + RMP alert)
          │
          ▼  [Tier 0–2: normal education flow]
 ┌─────────────────────────────────────────────────────┐
 │               Metadata Pre-filter                   │
 │   population type (T1/T2/GDM) × geography          │
 │   (Kerala > India > global) × topic tag             │
 └────────────────────────────┬────────────────────────┘
          │
          ▼
 ┌─────────────────────────────────────────────────────┐
 │              Vector DB — Semantic Retrieval         │
 │   Top-20 candidates from Tier 1 clinical corpus     │
 │   Embedding model: TBD (B1)                         │
 └────────────────────────────┬────────────────────────┘
          │
          ▼
 ┌─────────────────────────────────────────────────────┐
 │              Reranker                               │
 │   bge-reranker-large or Cohere Rerank               │
 │   Top-20 → Top-3 to 5 chunks                        │
 └────────────────────────────┬────────────────────────┘
          │
          ▼
 ┌─────────────────────────────────────────────────────┐
 │          Constraint Check (Rule Engine)             │
 │   Scope guardrails: no Rx, no diagnosis, no dose    │
 │   Drug-disease caution lookup (JSON table)          │
 │   Compliance namespace filter (never surface to     │
 │   patient)                                          │
 └────────────────────────────┬────────────────────────┘
          │
          ▼
 ┌─────────────────────────────────────────────────────┐
 │           LLM Response Generation                   │
 │   Retrieved chunks + patient context → response     │
 │   Literacy register: low / mid / high (English)     │
 │   Model: TBD (B1)                                   │
 └────────────────────────────┬────────────────────────┘
          │
          ▼
 ┌─────────────────────────────────────────────────────┐
 │           Response Post-processor                   │
 │   Safety boundary check (no dose slippage)          │
 │   Source attribution (which guideline)              │
 │   Conversation log to audit store                   │
 └────────────────────────────┬────────────────────────┘
          │
          ▼
   English Response → User
```

---

## Component Breakdown

### 1. Query Parser

**Input:** Raw English text  
**Output:** Structured intent object

```json
{
  "intent": "nutrition_education | drug_education | symptom_query | monitoring | complication_screening | fasting_protocol | general_dsmes",
  "topic_tags": ["glycemic", "foot", "medication", "nutrition", "renal", ...],
  "patient_context": {
    "diabetes_type": "T2DM",
    "comorbidities": ["CKD", "hypertension"],
    "current_medications": ["metformin", "glipizide"],
    "risk_tier_current": 1
  },
  "red_flag_signals": []
}
```

Intent classification runs before retrieval to set the metadata pre-filter correctly.

---

### 2. Risk Engine (runs in parallel, every turn)

**This is not RAG. It is a deterministic rule engine.**

Reads patient state from Postgres and evaluates hard-coded red-flag triggers:

| Trigger | Tier | Action |
|---------|------|--------|
| Hypoglycemia with seizure / unconsciousness | 4 | ER instructions + immediate RMP alert |
| Chest pain / breathlessness | 4 | ER instructions + immediate RMP alert |
| Suspected DKA symptoms | 4 | ER instructions + immediate RMP alert |
| Active foot ulcer / gangrene signs | 3 | Clinic within 24–48h + RMP notification |
| BG >300 mg/dL persisting | 3 | Clinic within 24–48h |
| Ramadan: BG <70 or >300 mg/dL | 3 | Break fast instruction + escalation |
| Vision changes, sudden | 3 | Ophthalmology referral |
| Persistent vomiting | 3 | Clinic within 24–48h |

- Tier 4 → bypass RAG entirely, return safety instructions only
- Tier 3 → append escalation notice to any RAG-generated response
- Tier 0–2 → continue normal education flow
- Risk score is logged to patient record every turn

**Implementation:** Hard-coded rule evaluation (~50–80 lines). No ML, no graph DB, no vector retrieval. Deterministic and fully auditable — required for SaMD Class B compliance.

---

### 3. Knowledge Store Architecture

**Decision: Vector DB only. No knowledge graph.**

Rationale: ~90% of the corpus is unstructured guideline text (PDFs) suited for semantic retrieval. The structured/relational data (escalation rules, drug cautions, nutrition tables) is simple enough to encode as rules and lookup tables — a knowledge graph would add operational complexity with no quality gain at current scope.

#### 3a. Vector DB — Clinical Corpus

| Source | Retrieval tier | Re-ingest cadence | india_specific | Parser class |
|--------|---------------|-------------------|---------------|-------------|
| RSSDI 2022 | core | Every 2–3 years | true | `RSSDirectParser` |
| ICMR STW 2024 | core | On new release | true | `ICMRWorkflowParser` |
| ADA 2026 (S01–S15) | core (fallback) | Annual (January) | false | `ADAJournalParser` |
| ICMR-NIN IFCT 2024 rev. | core | On new release | true | `ICMRNINParser` |
| Anoop Misra 2011 | core | Static | true | `ADAJournalParser` |
| KDIGO 2022 DM-CKD | triggered: ckd | On new release | false | `KDIGOParser` |
| IDF-DAR 2021 | triggered: ramadan | On new edition | true | `IDFDARParser` |
| ESC 2023 CV-DM | triggered: cardio | On new release | false | `ADAJournalParser` |
| WHO HEARTS | triggered: hypertension | On new release | false | `NarrativeParser` |
| Telemedicine Guidelines 2020 | compliance | On MoHFW amendment | true | `NarrativeParser` |

> DPDP Rules 2025 and ADA/ADCES DSMES 2022 live in `reference/` as team documents — never ingested.

**Chunking strategy:** By clinical recommendation unit — one chunk = one recommendation or one coherent clinical statement. Never chunk by token count. Tables are preserved as atomic units. Implemented in `ingestion/chunkers/` (next build step).

**Chunk metadata schema:**

```json
{
  "source": "RSSDI_2022",
  "year": 2022,
  "section_ref": "S4.1",
  "evidence_grade": "A",
  "population_scope": ["T2DM"],
  "age_scope": "adult",
  "topic_tags": ["medication", "metformin", "glycemic"],
  "retrieval_tier": "core",
  "condition_trigger": null,
  "india_specific": true
}
```

> No `geography_tag`. All patients are Kerala-based. `india_specific` is the only retrieval-relevant geography distinction — whether a source was calibrated for Indian physiology or is a global fallback.

**Retrieval priority:**
1. Population type match (T2DM query never returns T1DM chunk)
2. `india_specific: true` preferred over `false` when both available for same topic
3. Recency (newer guideline preferred if same population)
4. Semantic similarity score (after above filters applied)

#### 3b. Compliance Namespace — separate, never surfaced to patient

- Telemedicine Practice Guidelines India 2020 (`corpus/compliance/`)

Queried only by internal system logic (consent flows, audit checks, escalation SOPs). DPDP Rules 2025 and DSMES 2022 are reference-only documents in `reference/` — not ingested.

#### 3c. Structured Reference Tables (JSON / Postgres)

Not in the vector DB. Queried directly by the pipeline when needed:

**Nutrition lookup table** (from Kerala Nutrition Annex, post B2 sign-off):

```
Food item → carb content (g) → GI range → portion anchor (ladles) → glycemic load estimate
```

Example entries: matta rice, white parboiled rice, kappa, nendran banana, puttu, idli, dosa, appam, idiyappam, pathiri, chaaya (with sugar).

**Drug-disease caution table:**

```
Drug class → contraindication condition → eGFR threshold → action (educate / escalate / never mention dose)
```

Example: metformin → eGFR <30 → flag for Tier 2 escalation, educate on why doctor may review.

---

### 4. Metadata Pre-filter

Runs before vector similarity search. Applies hard exclusions:

- If `intent = nutrition_education` AND `patient = T2DM` → restrict to food/nutrition topic tags
- If `india_specific = true` sources available for topic → deprioritize `india_specific = false` sources
- If `retrieval_tier = compliance` → never include in patient-facing retrieval
- If `evidence_grade = E` (expert opinion) AND a Grade A source exists for same topic → prefer Grade A
- If `condition_trigger` is set → only include matching Tier 2 source for that trigger

This reduces the search space before semantic scoring, improving both precision and latency.

---

### 5. Reranker

**Input:** Top-20 chunks from vector similarity search  
**Output:** Top-3 to 5 chunks ranked by clinical relevance to query

**Model: `BAAI/bge-reranker-large`** — self-hosted via FlagEmbedding, configured in `config/settings.py`.

Reranking is a meaningful quality lever for clinical content — do not skip. Vector similarity alone retrieves plausible-sounding but sometimes clinically mismatched chunks.

---

### 6. Constraint Check

After reranking, before generation. Enforces hard scope boundaries:

- **No dose/titration content:** scan retrieved chunks for dose-specific language; strip or refuse if present
- **No diagnosis language:** retrieved content must not suggest a new diagnosis
- **Drug-disease caution flag:** if patient profile has CKD and retrieved chunk mentions SGLT2i → append standard caution note, flag for user to consult doctor
- **Compliance namespace isolation:** double-check no compliance document chunks leaked into patient-facing context

This is rule-based, not ML. Runs in <50ms.

---

### 7. LLM Response Generation

**Input:** Top-3 to 5 retrieved chunks + patient context + conversation history (last N turns) + system prompt  
**Output:** English response, literacy-register adapted

**System prompt enforces:**
- DSMES educator role (not doctor, not prescriber)
- Hard refusal phrases for out-of-scope requests
- Literacy register selection (low / mid / high) based on patient profile
- Source attribution format ("According to [ADA 2026 / RSSDI guidelines]…")
- Motivational Interviewing tone cues (OARS: Open questions, Affirmations, Reflections, Summaries)

**Conversation history:** Last N turns kept in context window to maintain continuity. N = TBD based on latency budget testing.

**Model: `claude-sonnet-4-6`** via Anthropic SDK (`config/settings.py`). Context window 200K tokens — sufficient for full conversation history plus retrieved chunks.

---

### 8. Response Post-processor

Final safety pass before response is returned:

1. **Scope check:** regex + classifier scan for dose numbers, diagnosis statements, medication substitution language → block and substitute with safe redirect
2. **Source tag:** append which guideline sourced the core claim (for clinician audit trail)
3. **Conversation log:** write full turn (query, retrieved chunks, response, risk tier, timestamp) to audit store (DPDP-compliant retention)
4. **Patient state update:** update risk tier and last-interaction timestamp in Postgres

---

## Data Stores Summary

| Store | Technology | What lives here |
|-------|-----------|-----------------|
| Vector DB | **Qdrant** (self-hosted, `docker-compose.yml`) | Clinical guideline chunks — clinical + compliance namespaces |
| Relational DB | **Postgres 16** (self-hosted, `docker-compose.yml`) | Patient profiles, risk tiers, conversation logs, audit records |
| Reference tables | JSON in Postgres | Nutrition carb/GI lookup, drug-disease cautions, monitoring schedules |

---

## What is NOT in Base Model Scope

| Feature | Where it goes |
|---------|--------------|
| Malayalam ASR / STT | Voice layer (post base model validation) |
| Code-mixed Malayalam-English handling | Language layer |
| Voice response (TTS) | Voice layer |
| Sugar Care Clinics EMR integration | B1/B6 |
| RMP notification delivery | B4 |
| Family member enrollment | Post-MVP |
| DPDP consent flow UI | B5 |

---

## Decisions Log

| # | Decision | Status | Resolution |
|---|----------|--------|------------|
| D1 | LLM model | **Done** | `claude-sonnet-4-6` |
| D2 | Embedding model | **Done** | `BAAI/bge-large-en-v1.5` |
| D3 | Vector DB platform | **Done** | Qdrant (self-hosted) |
| D4 | Reranker | **Done** | `BAAI/bge-reranker-large` |
| D5 | PDF parsing approach | **Done** | Custom parser per source — `ingestion/parsers/` |
| D6 | Conversation history window (N turns) | Open | Determine from latency testing |
| D7 | EMR integration design | Open | B1 remaining item |
| D8 | Nutrition table values (carb/GI) | Open | B2 clinical sign-off |
| D9 | Drug education monograph content | Open | B3 clinical sign-off |

---

## Design Principles

1. **Safety over completeness** — a response that says "please consult your doctor" is always better than one that guesses a dose or diagnosis.
2. **Deterministic safety, semantic education** — risk escalation is rule-based and auditable; education content is RAG-generated and source-attributed.
3. **India-first retrieval** — RSSDI/ICMR preferred over ADA when both cover the same topic for an Indian patient.
4. **No knowledge graph at this stage** — structured data (escalation rules, drug cautions, nutrition tables) is simple enough for rule engines and lookup tables; graph complexity is premature.
5. **Every turn is logged** — full audit trail required for SaMD Class B compliance and RMP oversight.
