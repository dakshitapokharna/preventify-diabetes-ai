# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project: Preventify Diabetes Educator AI (Kerala)

This is a pre-code repository. The `reference/` folder contains the clinical specification (v0.1) for an AI-powered diabetes educator system targeting Kerala, India. All engineering decisions must be grounded in those documents.

**Product summary:** A Malayalam-language, voice-first conversational AI that delivers DSMES (Diabetes Self-Management Education and Support) at the scope of a trained human diabetes educator. It feeds into the Sugar Care Clinics referral pipeline and is classified as SaMD Class B under CDSCO.

**North-star metric:** Cost per HbA1c-point reduction.

---

## Reference Documents

| File | Purpose |
|------|---------|
| `reference/diabetes_educator_intelligence_v0_1.docx` | Primary clinical specification — conversational scope, safety guardrails, escalation tiers, workflows, RAG architecture |
| `reference/guideline_corpus_sources.docx` | Three-tier corpus sourcing plan — which guidelines to ingest, metadata schema, retrieval logic |
| `reference/kerala_nutrition_annex_v0_1.docx` | Kerala-specific nutrition knowledge layer — local foods, festivals, fasting, monsoon protocols |
| `reference/DPDP_Rules_2025/` | Digital Personal Data Protection Rules 2025 — engineering + compliance reference for B5; governs consent design, data deletion path, breach protocol; do not ingest into any vector store |
| `reference/ADA_ADCES_DSMES_2022/` | ADA/ADCES 2022 National Standards for DSMES — build-time design reference; defines 7 DSMES topics, 4 critical time points, educator scope; use to shape conversational logic and escalation rules; do not ingest |
| `reference/Kerala_NCD_Aardram/` | Kerala State DHS T2DM Treatment Protocol 2021 — build-time alignment reference; use during B2 clinical sign-off to verify bot advice does not contradict what Kerala PHC/Aardram nurses tell the same patients; do not ingest — prescribing content would violate the no-drug-dose safety boundary |

Always read these before making decisions about clinical scope, data sourcing, or patient-facing behavior.

---

## Current Project Stage (as of 2026-05-21)

**Stage: Build — Chunking Pipeline Complete; Embedder + pgvector Upsert Next**

Specification phase is complete. Ingestion pipeline (extraction → chunking) is fully built and producing output. The next milestone is the embedder + pgvector upsert step, then the end-to-end English query → RAG response pipeline.

### What is locked and ready

| Area | Status |
|------|--------|
| Clinical scope boundary (DSMES only, no Rx/diagnosis) | Finalized |
| Risk escalation model (5 tiers, red-flag library) | Finalized |
| Knowledge corpus sources (2-tier retrieval model defined) | Finalized — all 10 PDFs downloaded; Tier 1 core: 5/5 complete; Tier 2 condition-triggered: 4/4 complete |
| RAG chunking strategy and retrieval logic | Finalized — see `CHUNKING_LOGIC.md` for full spec |
| Conversational architecture (3 literacy registers, MI scaffolds) | Finalized |
| Kerala nutrition knowledge layer (food-by-food, festivals, fasting, monsoon) | Detailed — 15 clinical placeholders remain |
| Compliance hooks (DPDP, Telemedicine Guidelines, SaMD Class B posture) | Framed — not yet filed |
| Tech stack — LLM, embedding, reranker, vector DB, PDF parsing | **Fully decided** — all locked, see B1 |
| PDF ingestion parsers (`ingestion/parsers/`) | **Built** — custom parser per source, all 10 corpus sources covered |
| Corpus extractors (`ingestion/extractors/`) | **Built + run** — all 10 parsed markdown files in `parsed/` |
| Chunking pipeline (`ingestion/chunkers/`) | **Built + run** — 4,059 chunks across 10 JSONL files in `data/chunks/`; token ceiling fully enforced (0 chunks over 512 tok, max exactly 512) |

### What is blocking full pipeline completion

| # | Blocker | Owner |
|---|---------|-------|
| B1 | Tech Stack Decisions — EMR integration only (all other decisions made) | Engineering |
| B2 | Clinical Sign-offs (Nutrition Placeholders) | Dr. Rakesh K R + RD |
| B3 | Drug Education Content | Clinical Lead |
| B4 | RMP Loop Design | Preventify Operations |
| B5 | SaMD Regulatory Pathway | Compliance |
| B6 | Operations & Clinic Handoff | Preventify Operations |

### Immediate next engineering step

Build the **embedder + pgvector upsert pipeline** (`ingestion/embedder/`). Full design is documented in the RAG System Design → Embedder section below. Summary:
1. Load each `data/chunks/*.jsonl`
2. Embed `text` field with `BAAI/bge-large-en-v1.5` in batches of 32
3. Upsert to pgvector table `preventify_corpus` (Neon/PostgreSQL) with full metadata payload
4. On re-run for a source: delete all existing rows for that source, re-insert fresh
5. Failed chunks logged to `logs/embed_failures.jsonl` — recoverable via `--retry-failed`
6. Add indexes on: `source`, `retrieval_tier`, `condition_trigger`, `india_specific`, `kerala_food`, `safety_critical`, `grade_priority`, `text_hash`

---

## Hard Clinical Constraints (Non-Negotiable)

The system must **never**:
- Recommend a specific drug dose or titration
- Substitute or stop a patient's medication
- Make a diagnosis
- Interpret lab results without clinical context
- Claim to be a doctor or RMP

These are SaMD safety boundaries. Any code path that could violate these — LLM prompts, RAG retrieval, response post-processing — must enforce them explicitly.

---

## Architecture

### Core Pipeline

```
Patient (Malayalam voice/text)
    → ASR (code-mixed Malayalam-English speech recognition)
    → Malayalam → English translation        ← all search/embed/generate works in English
    → [Risk engine runs in parallel — deterministic, no RAG]
    → Metadata pre-filter (population type, retrieval tier, india_specific)
    → Embedder: bge-large-en-v1.5 encodes English query → 1024-dim vector
    → pgvector ANN search → top-20 candidate chunks
    → Reranker: bge-reranker-large scores each (query, chunk) pair → top-5
    → Constraint check (no Rx/dose/diagnosis language)
    → Claude Sonnet 4.6 generates English response
    → English → Malayalam translation
    → Patient receives Malayalam response
    → Clinic referral when indicated → Sugar Care Clinics
```

**Key architectural decision — language layer is upstream of RAG:**
The entire RAG pipeline (embedding, vector search, reranking, generation) operates in English. Malayalam is handled exclusively at the edges — ASR + translation on input, translation on output. This means:
- `bge-large-en-v1.5` (English-only) is the correct embedder — no multilingual model needed
- The clinical corpus (all guidelines) is in English — query and corpus are in the same language space
- When Malayalam voice/translation is added, it slots in before and after the existing English pipeline — the RAG code does not change

### PDF Extraction — One-Time Run Pattern

Parsers in `ingestion/parsers/` are run **once per source PDF**. Output is written to `parsed/<SOURCE>.json` and kept permanently — these are the parsed artifacts that the chunker and embedder consume. There is no continuous re-parsing.

**To extract a single source:**
```
python extract_corpus.py RSSDI_2022
```

**To extract all sources:**
```
python extract_corpus.py
```

If the extracted JSON looks correct (block counts and sample text are sane), the file is done. If a PDF changes or a parser is fixed, delete the relevant `parsed/<SOURCE>.json` and re-run that source only.

The `parsed/` directory is gitignored (large JSON files). If you need to share parsed output, do so out-of-band.

### Docling Extractor Pattern (used for complex PDFs)

Four sources use Docling-based extractors — for two-column layouts, complex tables, or rotated headers that pdfplumber garbles. Two sources use pdfplumber-based extractors (faster, better suited to their structure). One compliance extractor handles the Telemedicine Guidelines.

| Extractor script | Backend | Source PDF | Output |
|-----------------|---------|-----------|--------|
| `ingestion/extractors/tier1/ada.py` | Docling | All 15 `ADA_2026_S*.pdf` in `corpus/tier1_clinical/ADA_2026/` | `parsed/ADA_2026_docling.md` |
| `ingestion/extractors/tier1/anoop_misra.py` | Docling | `corpus/tier1_clinical/Anoop_Misra_South_Asian_Nutrition/Anoop_Misra_Consensus_Dietary_Guidelines_Asian_Indians_2011.pdf` | `parsed/Anoop_Misra_docling.md` |
| `ingestion/extractors/tier1/icmr_stw.py` | Docling | `corpus/tier1_clinical/ICMR_STW_2024/ICMR_STW_Diabetes_T2DM_2024.pdf` | `parsed/ICMR_STW_2024_docling.md` |
| `ingestion/extractors/tier1/icmr_nin.py` | pdfplumber | `corpus/tier1_clinical/ICMR_NIN/ICMR_NIN_Indian_Food_Composition_Tables.pdf` | `parsed/ICMR_NIN_docling.md` |
| `ingestion/extractors/tier1/rssdi.py` | pdfplumber | `corpus/tier1_clinical/RSSDI_2022/RSSDI_Clinical_Practice_Recommendations_T2DM_2022.pdf` | `parsed/RSSDI_2022_docling.md` |
| `ingestion/extractors/tier2/esc_2023.py` | Docling | `corpus/tier2_condition/ESC_2023_CV_DM/ESC_2023_CVD_Diabetes_Guidelines.pdf` | `parsed/ESC_2023_CVD_DM_docling.md` |
| `ingestion/extractors/tier2/idf_dar.py` | pdfplumber | `corpus/tier2_condition/IDF_DAR/IDF_DAR_Practical_Guidelines_Diabetes_Ramadan.pdf` | `parsed/IDF_DAR_Ramadan_docling.md` |
| `ingestion/extractors/tier2/kdigo_2022.py` | pdfplumber | `corpus/tier2_condition/KDIGO_2022_DM_CKD/KDIGO_2022_Diabetes_Management_in_CKD.pdf` | `parsed/KDIGO_2022_DM_CKD_docling.md` |
| `ingestion/extractors/tier2/who_hearts.py` | Docling | `corpus/tier2_condition/WHO_HEARTS/WHO_HEARTS_Technical_Package.pdf` | `parsed/WHO_HEARTS_docling.md` |
| `ingestion/extractors/compliance/telemedicine.py` | Docling | `corpus/compliance/Telemedicine_Practice_Guidelines_India_2020.pdf` | `parsed/Telemedicine_Guidelines_India_2020.md` |

**Note on backends:** ICMR-NIN, IDF-DAR, and KDIGO use pdfplumber (Docling crashes with `std::bad_alloc` on their dense multi-hundred-page PDFs). All others use Docling. See individual extractor scripts for annotation logic.

**To re-run any extractor:**
```
python ingestion/extractors/tier1/ada.py
python ingestion/extractors/tier1/anoop_misra.py
python ingestion/extractors/tier1/icmr_stw.py
python ingestion/extractors/tier1/icmr_nin.py
python ingestion/extractors/tier1/rssdi.py
python ingestion/extractors/tier2/esc_2023.py
python ingestion/extractors/tier2/idf_dar.py
python ingestion/extractors/tier2/kdigo_2022.py
python ingestion/extractors/tier2/who_hearts.py
python ingestion/extractors/compliance/telemedicine.py
```

---

### RAG System Design

**Chunking pipeline is built.** See `CHUNKING_LOGIC.md` for the full specification and `CHUNKING_DISCUSSION.md` for design rationale. Implementation lives in `ingestion/chunkers/`. Output: `data/chunks/*.jsonl`.

**To run the chunker pipeline:**
```
python ingestion/chunkers/run.py               # all sources
python ingestion/chunkers/run.py RSSDI_2022    # single source
python ingestion/chunkers/run.py --dry-run     # count only, no files written
```

**Chunker types and current output counts:**

| Source | Chunker | Chunks |
|--------|---------|--------|
| RSSDI_2022 | `recommendation` | 764 |
| ICMR_STW_2024 | `recommendation` | 10 |
| ADA_2026 | `recommendation` | 658 |
| ICMR_NIN | `food_table` | 71 |
| Anoop_Misra_South_Asian_Nutrition | `narrative` | 58 |
| KDIGO_2022_DM_CKD | `page_window` | 891 |
| IDF_DAR | `page_window` | 1,103 |
| ESC_2023_CV_DM | `recommendation` | 419 |
| WHO_HEARTS | `narrative` | 20 |
| Telemedicine_Guidelines_2020 | `narrative` | 65 |
| **Total** | | **4,059** |

**Chunk metadata schema** — 14 fields, patient-first (see `CHUNKING_LOGIC.md` for full spec):
```json
{
  "chunk_id":          "a3f2b89c1d4e5f67",
  "source":            "RSSDI_2022",
  "year":              2022,
  "section_title":     "Glycemic Targets",
  "text":              "[RSSDI 2022 — Glycemic Targets]\n\nHbA1c target...",
  "retrieval_tier":    "core",
  "condition_trigger": null,
  "india_specific":    true,
  "kerala_food":       false,
  "safety_critical":   false,
  "grade_priority":    1,
  "meal_context":      null,
  "text_hash":         "sha256[:32]",
  "token_estimate":    87
}
```

**`retrieval_tier`:** `core` (Tier 1 — every turn) | `triggered` (Tier 2 — condition flag only) | `compliance`  
**`condition_trigger`:** `null` for Tier 1 | `ckd` | `cardio` | `ramadan` | `hypertension`  
**`india_specific`:** `true` = RSSDI/ICMR/ICMR-NIN; `false` = ADA/ESC/KDIGO/WHO  
**`grade_priority`:** 1 (strongest) → 5 (consensus/ungraded) — pre-filter for safety-critical queries only  
**`kerala_food`:** `true` on ICMR-NIN Type B individual Kerala food row chunks only  
**`token_estimate`:** `len(text) // 4` — cost tracking

**Retrieval logic** (hard rule — not a preference):

*Tier 1 — every turn:*
1. **RSSDI 2022 / ICMR STW 2024** — India-specific, checked first for all standard T2DM queries
2. **ADA 2026** — fallback when India sources are silent; critical for elderly protocols (S12), CGM, hypoglycemia detail; re-ingest annually
3. **ICMR-NIN / Anoop Misra** — always active for any nutrition or food query

*Tier 2 — triggered only when flag fires:*
- **KDIGO 2022** — CKD flag (kidney / creatinine / eGFR / dialysis); overrides all Tier 1 sources on CKD queries
- **IDF-DAR** — Ramadan flag (Ramadan / roza / religious fasting)
- **ESC 2023** — Cardio flag (heart disease / cardiovascular / angina / heart failure)
- **WHO HEARTS** — Hypertension flag (triggered alongside ESC for BP-primary queries)

**Embedder pipeline** — `ingestion/embedder/` (next build step):

*Database — pgvector table `preventify_corpus` on Neon (PostgreSQL):*
```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE preventify_corpus (
    id                SERIAL PRIMARY KEY,
    chunk_id          TEXT UNIQUE NOT NULL,
    source            TEXT NOT NULL,
    year              INT,
    section_title     TEXT,
    text              TEXT NOT NULL,
    embedding         vector(1024),
    retrieval_tier    TEXT,
    condition_trigger TEXT,
    india_specific    BOOLEAN,
    kerala_food       BOOLEAN,
    safety_critical   BOOLEAN,
    grade_priority    INT,
    meal_context      TEXT,
    token_estimate    INT,
    text_hash         TEXT,
    inserted_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ON preventify_corpus (source);
CREATE INDEX ON preventify_corpus (retrieval_tier);
CREATE INDEX ON preventify_corpus (condition_trigger);
CREATE INDEX ON preventify_corpus (india_specific);
CREATE INDEX ON preventify_corpus (kerala_food);
CREATE INDEX ON preventify_corpus (safety_critical);
CREATE INDEX ON preventify_corpus (grade_priority);
CREATE INDEX ON preventify_corpus (text_hash);
```

*Connection:* `DATABASE_URL` in `.env` at project root (never committed). Loaded via `python-dotenv`.

*Run modes:*
```
python ingestion/embedder/run.py                  # embed all 10 sources
python ingestion/embedder/run.py RSSDI_2022       # single source only
python ingestion/embedder/run.py --dry-run        # count what would be inserted, no writes
python ingestion/embedder/run.py --retry-failed   # re-try chunks in logs/embed_failures.jsonl
```

*Re-run behaviour (single source):* Delete all existing rows for that source first, then re-insert everything fresh. This is the correct approach for guideline updates (e.g. ADA 2027 replaces ADA 2026).

*Batch size:* 32 chunks per embedding call — safe for CPU, ~2–4 GB RAM.

*Failure handling and recovery:*
- If a chunk fails to embed or insert, it is logged to `logs/embed_failures.jsonl` and the run continues
- Each failure line contains the **full chunk JSON** (all 14 fields) plus `error` message and `failed_at` timestamp — enough to re-process without reading the original JSONL again
- `--retry-failed` reads `logs/embed_failures.jsonl`, re-embeds each chunk, upserts by `chunk_id` (`INSERT ... ON CONFLICT (chunk_id) DO UPDATE`) — this places the chunk at its correct position in the DB regardless of when it is re-run
- On successful retry → entry removed from `logs/embed_failures.jsonl`
- On failed retry → error and timestamp updated in place, kept in log
- Run ends with a summary: total inserted, total skipped, total still failing

```json
// Example logs/embed_failures.jsonl entry
{
  "chunk_id": "a3f2b89c1d4e5f67",
  "source": "RSSDI_2022",
  "error": "ConnectionError: timed out after 30s",
  "failed_at": "2026-05-23T14:32:01Z",
  "chunk": {
    "chunk_id": "a3f2b89c1d4e5f67",
    "source": "RSSDI_2022",
    "year": 2022,
    "section_title": "Glycemic Targets",
    "text": "[RSSDI 2022 — Glycemic Targets]\n\nHbA1c target < 7.0%...",
    "retrieval_tier": "core",
    "condition_trigger": null,
    "india_specific": true,
    "kerala_food": false,
    "safety_critical": false,
    "grade_priority": 1,
    "meal_context": null,
    "token_estimate": 87,
    "text_hash": "a3f2b89c1d4e5f67890abcd"
  }
}
```

*Dependencies:* `sentence-transformers`, `psycopg2-binary`, `pgvector`, `python-dotenv`, `tqdm`

**Two-stage retrieval — embedder then reranker (both required, do not skip either):**

*Stage 1 — Embedder (fast, approximate):*
`BAAI/bge-large-en-v1.5` encodes the English query into a 1024-dim vector. pgvector ANN search runs against all ~4,059 stored chunk vectors and returns **top-20 candidates** in milliseconds. This is fast but approximate — cosine similarity compares query and chunk *independently*, so it can return plausible-sounding but clinically mismatched chunks.

*Stage 2 — Reranker (slower, precise):*
`BAAI/bge-reranker-large` (self-hosted via FlagEmbedding) is a **cross-encoder** — it takes the query and each of the 20 chunks *together as a pair* and outputs a single relevance score per pair. Because it reads both simultaneously, it understands the relationship between query and chunk, not just surface similarity. The 20 pairs are scored, sorted, and the **top-5** are passed to Claude.

```
query + chunk_1  →  bge-reranker-large  →  score: 0.91  ✓ keep
query + chunk_2  →  bge-reranker-large  →  score: 0.43  ✗ drop
...
query + chunk_20 →  bge-reranker-large  →  score: 0.67  ✓ keep
```

*Why two stages instead of reranker alone:*
Running a cross-encoder over all 4,059 chunks on every query would be too slow — each pair requires a full model forward pass. The embedder narrows the search space cheaply; the reranker applies expensive precision only to the shortlist.

*Why bge-large + bge-reranker-large pair:*
Both are from the BAAI family, trained on the same data distribution. The reranker was fine-tuned to re-score candidates that a BGE embedder retrieves — using the same family avoids distribution mismatch.

**Namespacing:** Only the Telemedicine Practice Guidelines go into the `compliance` namespace — the bot queries this in real time to enforce scope boundaries. DPDP Rules 2025 and DSMES 2022 are not corpus sources; they live in `reference/` as team documents and must not be ingested into any namespace.

### Knowledge Corpus

All corpus files live in `corpus/`. See `corpus/README.md` for full download status, source URLs, and update cadence.

**Tier 1 — Core always-active RAG — `corpus/tier1_clinical/` — queried on every turn:**

| Folder | Document | Role | Status |
|--------|----------|------|--------|
| `RSSDI_2022/` | RSSDI Clinical Practice Recommendations for T2DM 2022 | Primary — first-choice for all standard T2DM queries | Downloaded |
| `ICMR_STW_2024/` | ICMR Standard Treatment Workflow for T2DM 2024 | Primary — most current GoI clinical decision flow | Downloaded |
| `ADA_2026/` | ADA Standards of Care in Diabetes 2026 (S01–S15) | Fallback — fills gaps India sources don't cover; re-ingest annually each January | Downloaded |
| `ICMR_NIN/` | ICMR-NIN Indian Food Composition Tables (IFCT 2017, Dec 2024 rev.) | Nutrition anchor — authoritative carb/GI values for all Indian and Kerala foods | Downloaded |
| `Anoop_Misra_South_Asian_Nutrition/` | Consensus Dietary Guidelines for Asian Indians (Misra et al., 2011) | South Asian body baseline — corrects Western BMI/carb assumptions for all patients | Downloaded |

**Tier 2 — Condition-triggered RAG — `corpus/tier2_condition/` — queried only when trigger flag fires:**

| Folder | Document | Trigger | Status |
|--------|----------|---------|--------|
| `KDIGO_2022_DM_CKD/` | KDIGO 2022 Guideline for Diabetes Management in CKD | CKD flag — overrides all Tier 1 sources | Downloaded |
| `IDF_DAR/` | IDF-DAR Practical Guidelines for Diabetes and Ramadan (2021) | Ramadan flag | Downloaded |
| `ESC_2023_CV_DM/` | ESC 2023 Guidelines on CV Disease in Diabetes | Cardio flag | Downloaded |
| `WHO_HEARTS/` | WHO HEARTS Technical Package | Hypertension flag | Downloaded |
| `Kerala_NCD_Aardram/` | Kerala State DHS T2DM Treatment Protocol 2021 | ~~Kerala protocol flag~~ | **Moved to `reference/`** — prescribing workflow for PHC doctors, not patient education content; use during B2 sign-off only, do not ingest |

**Compliance namespace — `corpus/compliance/` — background scope enforcement, never shown to patient:**

| Folder | Document | Status |
|--------|----------|--------|
| `Telemedicine_Practice_Guidelines_India_2020.pdf` | Telemedicine Practice Guidelines India 2020 | Downloaded |

### Risk Escalation Tiers

| Tier | Description | Action |
|------|-------------|--------|
| 0 | Education only | Continue conversation |
| 1 | Low concern | Nudge to next scheduled clinic visit |
| 2 | Moderate concern | Clinic within 1–2 weeks |
| 3 | High concern | Clinic within 24–48 hours |
| 4 | Same-day emergency | Parallel: patient safety instructions + immediate RMP notification |

Risk scoring runs silently in the background on every conversation turn.

### Conversational Intelligence

- **Three literacy registers:** low / mid / high — switch dynamically based on patient cues
- **Motivational Interviewing:** OARS principles, stages of change
- **Family as clinical unit:** family members enrollable with patient consent
- **Faith/fasting handling:** Ramadan (IDF-DAR stratification), Ekadashi, Lent, Navratri — with clinical guardrails on each

### Lead Capture Architecture (v0.3)

Full spec: `preventify lead architecture v2.pdf`. The bot has a lead capture layer that runs silently alongside the clinical education layer. Patients never experience it as a sales flow — it is designed to feel like a natural continuation of the conversation.

**Build principles (non-negotiable):**
- **Safety first** — clinical escalation always overrides lead capture routing
- **Cost** — lean token footprint; save tokens in chunking, prompts, and memory retrieval
- **Lean footprint** — single PostgreSQL (Neon) for all data: vectors, user memory, lead data; no separate services
- **User-level rate limiting** — to be implemented once the database schema is set

**Agentic decision loop — every message turn:**
```
Receive message
→ Recall user memory (identity + clinical profile + lifetime score)
→ Classify: assign Question Depth Score (QDS 1–5) via LLM
→ Route: answer from RAG / escalate clinically / trigger consent / update lead
→ Respond + update memory and lifetime score
```

**Question Depth Score (QDS):**

| Score | Intent | Example |
|-------|--------|---------|
| 1 | General awareness | "What is HbA1c?" |
| 2 | Personal relevance | "My HbA1c came back at 7.2 — is that okay?" |
| 3 | Active management | "Should I take metformin before or after food?" |
| 4 | Complication concern | "My feet go numb at night — is that from diabetes?" |
| 5 | Complex / distressed | "My doctor wants to put me on insulin. I'm scared." |

QDS is assigned by the LLM agent — not keyword matching. Dr. Rakesh must validate classification on 50 real questions before go-live.

**Volume decay — prevents score inflation from low-depth questions:**
- Q1–Q4 (low depth): 1.0× full value
- Q5–Q7 (low depth): 0.5×
- Q8+ (low depth): 0.25×
- QDS 3, 4, 5: always 1.0× regardless of volume

**Persistent user memory — 3 layers:**
- Layer 1 — Identity: WhatsApp number (primary key), name, age, first contact date
- Layer 2 — Clinical profile: detected diabetes type, complications mentioned, medications referenced, highest QDS ever asked
- Layer 3 — Engagement: lifetime score (with decay), number of sessions, recency weight, consent status + timestamp, lead status

Recency weighting: current session = 1.0×, last 30 days = 0.8×, older = 0.5×.

**Capture trigger — both conditions must be true simultaneously:**
- Condition A: lifetime score ≥ 8 (with decay applied)
- Condition B: at least one QDS 3+ question in lifetime history
- Minimum floor: 3 total messages lifetime before capture can fire
- Clinical escalation questions (hypoglycemia, chest pain, acute symptoms) are never routed into capture — always follow clinical escalation path

**Consent moment:**
- Agent waits for a natural pause after delivering a full answer — never interrupts mid-answer
- Consent message must reference a specific topic from the conversation (LLM-generated, never templated)
- Collects only name + age in-chat; WhatsApp number already captured
- DPDP Act 2023 compliant — user told explicitly what data is used for and by whom; timestamp stored
- If declined: no re-prompt in same session; returning users who declined can be re-prompted once when lifetime score exceeds 12

**AI brief — generated at consent, pushed to CRM via webhook:**
- Identity (name, number, age)
- Detected condition type (T1/T2/GDM/Prediabetes/Undiagnosed)
- Concern summary — 2–3 lines, LLM-generated from full conversation history, never templated
- Engagement score at capture
- Peak concern (highest QDS topic ever raised)
- Capture timestamp (IST)

**Sales pipeline — 4 stages:**
New Lead → Contacted → Qualified → Converted/Closed

Recommended CRM: Zoho CRM Free or HubSpot Starter. Chatbot pushes leads via webhook on consent — no manual entry.

---

## Kerala-Specific Knowledge Layer

This is a key differentiator. All nutrition, lifestyle, and cultural handling must be Kerala-aware:

- **Rice:** matta and white parboiled varieties; portion anchors in **ladles** (not cups — Kerala patients do not use cups)
- **Staples:** kappa (tapioca), nendran banana, jackfruit, puttu, idli, dosa, appam — each with carb estimates
- **Coconut fat:** nuanced guidance, never blanket prohibition (culturally non-negotiable); formal clinical position pending (B2)
- **Fish:** mathi, ayala, karimeen, netholi — actively encouraged as cardiometabolic assets
- **Chaaya (sweet tea):** single highest-yield dietary intervention — 4–8 cups/day with sugar is often the largest hidden sugar source
- **Festivals:** Onam sadhya, Vishu, Christmas, Eid, Bakrid — each has a specific eating strategy
- **Ramadan:** IDF-DAR risk stratification (very high / high / moderate / low), suhoor/iftar guidance, SMBG thresholds (break fast if BG <70 or >300 mg/dL); adoption of IDF-DAR thresholds verbatim vs adapted pending (B2)
- **Monsoon protocols:** foot care escalation, indoor activity alternatives, insulin storage during power cuts
- **Gulf-migrant context:** remote family stakeholders, solo-living elderly patients

---

## Build Blockers

Each blocker is self-contained and can be picked up independently in a new chat. Start any session by stating which blocker you are working on.

---

### B1 — Tech Stack Decisions
**Owner:** Engineering  
**Dependency:** None  
**Status:** Mostly resolved — one open item remains

| Decision | Status | Choice |
|----------|--------|--------|
| LLM | **Decided** | `claude-sonnet-4-6` via Anthropic SDK |
| Embedding model | **Decided** | `BAAI/bge-large-en-v1.5` (sentence-transformers) |
| Reranker | **Decided** | `BAAI/bge-reranker-large` (self-hosted, top-20 → top-5) |
| Vector store | **Decided** | pgvector on Neon (PostgreSQL) — single DB for vectors, user memory, and lead data; no Qdrant |
| PDF parsing | **Decided + built** | Custom parser per source — `ingestion/parsers/`; pdfplumber for all sources |
| Risk scoring engine | **Decided** | Hard-coded rule engine, deterministic, no ML, <500ms target |
| ASR strategy | Open — post base model | Evaluate Whisper fine-tuned on Malayalam vs Google STT |
| EMR integration | **Open** | How bot connects to Sugar Care Clinics' patient records; API design, auth, sync frequency |

**One remaining open decision:** EMR integration design (item 5). All other stack decisions are locked.

**Starting context for a new chat:** Read `base_model_spec.md`. The remaining task is EMR integration design — API contract between bot and Sugar Care Clinics EMR, auth method, patient record sync frequency, and what patient data the bot needs at session start.

---

### B2 — Clinical Sign-offs (Nutrition Placeholders)
**Owner:** Dr. Rakesh K R + Registered Dietitian  
**Dependency:** None — clinical review can happen in parallel with B1

The Kerala Nutrition Annex (`reference/kerala_nutrition_annex_v0_1.docx`) has ~15 explicit placeholders that must be validated before the system can go live. Key items:

1. **Rice portion sizes** — preferred cooked rice portion for tight control (30g vs. 50–60g per meal); confirm in ladle units
2. **GI values** — glycemic index ranges for matta rice, white parboiled, kappa, nendran banana, and common breakfast items are best-effort approximations; validate against ICMR-NIN tables and Anoop Misra South Asian consensus
3. **Coconut clinical position** — no formal Preventify statement yet on when/how much coconut reduction is advised per lipid profile; needs one signed paragraph the system can quote consistently
4. **Ramadan stratification** — does Preventify adopt IDF-DAR 2021 risk thresholds verbatim (very high/high/moderate/low) or adapt for their patient population? System needs one clear decision tree
5. **Breakfast carb estimates** — table of puttu, idli, dosa, appam, idiyappam, pathiri estimates are serving-size dependent; RD to validate
6. **Quick carb reference table** — all values in the annex are approximate; validate against ICMR-NIN and Kerala-specific sources
7. **Patient-facing teaching templates** — four templates (rice, tea, coconut, foot care) need clinical accuracy approval and Malayalam-translation suitability check
8. **Missing dishes** — identify any Kerala-specific foods or eating situations not yet covered in the annex

**Starting context for a new chat:** Read `reference/kerala_nutrition_annex_v0_1.docx` in full. The task is to go through every yellow-highlighted placeholder and produce a validation checklist with proposed values for Dr. Rakesh and the RD to confirm or revise.

---

### B3 — Drug Education Content
**Owner:** Clinical Lead (Dr. Rakesh K R)  
**Dependency:** None — can run in parallel

The spec defines that the system must be able to educate patients on drug classes at educator level (mechanisms, side effects, storage, injection technique) but must never recommend dose changes. No approved content exists yet per molecule.

Deliverable required:
- Per drug-class monographs for: Metformin, Sulfonylureas, DPP-4 inhibitors, SGLT2 inhibitors, GLP-1 RAs, Basal insulin, Premixed insulin
- Each monograph must cover: mechanism in plain Malayalam, common side effects, storage instructions, what to do if a dose is missed, red flags requiring RMP escalation
- Line-by-line sign-off by clinical lead before ingestion into RAG corpus
- Must also define drug-disease contraindication scope: what the system can state vs. what it must escalate

**Starting context for a new chat:** Read `reference/diabetes_educator_intelligence_v0_1.docx` Section on pharmacology knowledge. The task is to draft the per-drug-class educator-level monograph template and produce a first draft for each drug class listed above, flagging anything that requires clinical lead review.

---

### B4 — RMP Loop Design
**Owner:** Preventify Operations  
**Dependency:** Partially depends on B1 (EMR integration approach)

A named Registered Medical Practitioner must be on record for audit, escalation receipt, and clinical content review. The current spec defines the need but not the implementation.

Decisions required:
1. **Named RMP list** — which RMPs are in-loop, their availability windows, and backup coverage
2. **Escalation acknowledgment SLA** — how quickly must a Tier 3/4 notification be acknowledged; what happens if not acknowledged in time
3. **Sample conversation review SOP** — how many conversations reviewed per week/month, by whom, and what triggers a full audit
4. **Post-escalation closure** — criteria for closing an escalated case; who marks it resolved
5. **RMP notification channel** — SMS, app notification, email, or integrated into Sugar Care Clinics EMR

**Starting context for a new chat:** Read the Risk Escalation Tiers and RMP-in-the-loop sections in `reference/diabetes_educator_intelligence_v0_1.docx`. The task is to design the full RMP operational loop: notification flow, SLA, review cadence, and closure criteria.

---

### B5 — SaMD Regulatory Pathway
**Owner:** Compliance  
**Dependency:** None — can run in parallel

The system is anticipated to be SaMD Class B under CDSCO but this is not yet formally confirmed. No regulatory submission has been prepared.

Items required:
1. **SaMD classification confirmation** — formal Class B determination; assess if any feature (e.g., risk scoring engine) could push to Class C
2. **CDSCO registration pathway** — full submission document; medical device registration steps under MDR 2017
3. **Clinical content liability framework** — who indemnifies what; RMP accountability scope; how system errors are handled
4. **DPDP 2023 data architecture** — finalize consent versioning design, data deletion path, and breach protocol; DPDP Rules enforcement timeline is phased 12–18 months from November 2025
5. **Data Fiduciary documentation** — template for Preventify's role as Data Fiduciary under DPDP 2023
6. **Conversation logging compliance** — retention period, audit access controls, anonymization approach

**Starting context for a new chat:** Read the Regulatory Context section in this file and the compliance-relevant sections of `reference/diabetes_educator_intelligence_v0_1.docx`. The task is to produce a regulatory checklist and identify the critical path to CDSCO registration and DPDP compliance before go-live.

---

### B6 — Operations & Clinic Handoff
**Owner:** Preventify Operations  
**Dependency:** Partially depends on B4 (RMP loop)

The referral pipeline into Sugar Care Clinics is central to the product's value proposition, but the operational design is unspecified.

Decisions required:
1. **Sugar Care Clinic capacity** — how many referrals per day/week can the clinics absorb; what happens when at capacity
2. **Referral handoff SOP** — exact data handed off at referral (patient profile, conversation summary, risk tier, reason for referral); format and delivery mechanism
3. **Patient cohort definitions** — which patients are eligible for the bot; screening criteria; exclusions (e.g., T1DM, GDM, active foot ulcer)
4. **Pricing transparency content** — what the bot tells patients about clinic visit costs; what it can and cannot say about affordability/waiver programs
5. **Family enrollment policy** — consent flow when a family member (including Gulf-based relatives) is enrolled as a support contact
6. **Patient acquisition funnel** — how patients first enter the system (clinic referral, self-enrollment, community health worker, etc.)

**Starting context for a new chat:** Read the onboarding and clinic referral workflow sections in `reference/diabetes_educator_intelligence_v0_1.docx`. The task is to design the end-to-end operations for patient intake, referral handoff, and clinic capacity management.

---

## Regulatory Context

- **Classification:** SaMD Class B, CDSCO India (anticipated — formal confirmation pending, see B5)
- **Data protection:** DPDP Act 2023 — consent must be versioned, data must have a deletion path, breach protocol required
- **RMP-in-the-loop:** A named Registered Medical Practitioner must be on record for audit, escalation receipt, and clinical content review (design pending, see B4)
- **Conversation logging:** Required for audit; retention must be DPDP-compliant
