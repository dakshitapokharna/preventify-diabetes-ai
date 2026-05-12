# Base Model Specification — Preventify Diabetes Educator AI

**Version:** 0.1  
**Date:** 2026-05-12  
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

#### 3a. Vector DB — Clinical Corpus (Tier 1)

| Source | Re-ingest cadence | Population filter | Geography tag |
|--------|------------------|-------------------|---------------|
| ADA Standards of Care 2026 | Annual (January) | T1DM, T2DM, GDM | global |
| RSSDI-ESI CPR for T2DM 2022 | Every 2–3 years | T2DM | India |
| ICMR Guidelines T2DM 2018 | On new release | T2DM | India |
| ICMR STW T2DM 2024 | On new release | T2DM | India |
| ADA/ADCES DSMES Standards 2022 | Every 2–3 years | T2DM | global |
| KDIGO 2022 Diabetes in CKD | On new release | T2DM+CKD | global |
| KDIGO 2024 CKD Guideline | On new release | CKD | global |
| IDF Atlas 11th Ed. 2025 | On new edition | epidemiology only | global |
| Kerala Nutrition Annex (internal) | On clinical sign-off | T2DM | Kerala |
| Drug education monographs (internal) | On clinical sign-off | T2DM | India |

**Chunking strategy:** By clinical recommendation unit — one chunk = one recommendation or one coherent clinical statement. Never chunk by token count. Tables must be preserved as atomic units.

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
  "geography_tag": "India"
}
```

**Retrieval priority:**
1. Population type match (T2DM query never returns T1DM chunk)
2. India/Kerala-specific source preferred over global when both available
3. Recency (newer guideline preferred if same geography and population)
4. Semantic similarity score (after above filters applied)

#### 3b. Compliance Namespace (Tier 2) — separate, never surfaced to patient

- Telemedicine Practice Guidelines India 2020
- DPDP Act 2023 + Rules 2025
- MoHFW GDM/DIPSI Guidelines 2018
- SaMD regulatory documents

Queried only by internal system logic (consent flows, audit checks, escalation SOPs).

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

- If `intent = nutrition_education` AND `patient = T2DM` → exclude T1DM-specific chunks, exclude IDF Atlas (epidemiology tag)
- If `geography_available = India` → deprioritize global-only sources
- If `compliance namespace` → never include in patient-facing retrieval
- If `evidence_grade = E` (expert opinion) AND a Grade A source exists for same topic → prefer Grade A

This reduces the search space before semantic scoring, improving both precision and latency.

---

### 5. Reranker

**Input:** Top-20 chunks from vector similarity search  
**Output:** Top-3 to 5 chunks ranked by clinical relevance to query

**Model options (B1 decision):**
- `bge-reranker-large` — open source, self-hosted, lower latency
- `Cohere Rerank` — managed API, strong multilingual performance (relevant for later Malayalam phase)

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

**Conversation history:** Last N turns kept in context window to maintain continuity. N = TBD based on model context window and latency budget (B1).

**Model selection:** TBD (B1). Key requirements: strong instruction-following, low hallucination rate on clinical content, context window ≥ 16K tokens.

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
| Vector DB | Pinecone / Weaviate / pgvector (B1) | Clinical guideline chunks (Tier 1) |
| Compliance namespace | Separate vector DB namespace | Tier 2 regulatory docs |
| Relational DB | Postgres | Patient profiles, risk tiers, conversation logs, audit records |
| Reference tables | JSON (embedded) or Postgres | Nutrition carb/GI lookup, drug-disease cautions, monitoring schedules |

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

## Open Decisions (Blocking Build)

| # | Decision | Blocker |
|---|----------|---------|
| D1 | LLM model selection | B1 |
| D2 | Embedding model selection | B1 |
| D3 | Vector DB platform (Pinecone vs Weaviate vs pgvector) | B1 |
| D4 | Reranker choice (bge-reranker-large vs Cohere) | B1 |
| D5 | Conversation history window size (N turns) | B1 |
| D6 | Nutrition table values (carb/GI) | B2 clinical sign-off |
| D7 | Drug education monograph content | B3 clinical sign-off |

---

## Design Principles

1. **Safety over completeness** — a response that says "please consult your doctor" is always better than one that guesses a dose or diagnosis.
2. **Deterministic safety, semantic education** — risk escalation is rule-based and auditable; education content is RAG-generated and source-attributed.
3. **India-first retrieval** — RSSDI/ICMR preferred over ADA when both cover the same topic for an Indian patient.
4. **No knowledge graph at this stage** — structured data (escalation rules, drug cautions, nutrition tables) is simple enough for rule engines and lookup tables; graph complexity is premature.
5. **Every turn is logged** — full audit trail required for SaMD Class B compliance and RMP oversight.
