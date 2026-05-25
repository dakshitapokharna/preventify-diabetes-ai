# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project: Preventify Diabetes Educator AI (Kerala)

**Product summary:** A Malayalam-language, voice-first conversational AI that delivers DSMES (Diabetes Self-Management Education and Support) at the scope of a trained human diabetes educator. It feeds into the Sugar Care Clinics referral pipeline and is classified as SaMD Class B under CDSCO.

**North-star metric:** Cost per HbA1c-point reduction.

---

## Reference Documents

| File | Purpose |
|------|---------|
| `reference/diabetes_educator_intelligence_v0_1.docx` | Primary clinical specification — conversational scope, safety guardrails, escalation tiers, workflows, RAG architecture |
| `reference/guideline_corpus_sources.docx` | Three-tier corpus sourcing plan — which guidelines to ingest, metadata schema, retrieval logic |
| `reference/kerala_nutrition_annex_v0_1.docx` | Kerala-specific nutrition knowledge layer — local foods, festivals, fasting, monsoon protocols |
| `reference/DPDP_Rules_2025/` | DPDP Rules 2025 — compliance reference for B5; governs consent design, data deletion, breach protocol; **do not ingest** |
| `reference/ADA_ADCES_DSMES_2022/` | ADA/ADCES 2022 DSMES Standards — build-time design reference; **do not ingest** |
| `reference/Kerala_NCD_Aardram/` | Kerala State DHS T2DM Protocol 2021 — B2 sign-off reference only; **do not ingest** |

Always read these before making decisions about clinical scope, data sourcing, or patient-facing behavior.

---

## Current Project Stage (as of 2026-05-24)

**Stage: Build — Phase 1 Context Engine Complete; Phase 1 Orchestrator (`engine/phase1.py`) Next**

**Channel strategy:** Tested via a **web-based chat frontend** (not WhatsApp) during base-model validation. WhatsApp integration happens only after clinical sign-off. See `BOT_CONVERSATION_ARCHITECTURE.md` Section 2.

### What is locked and ready

| Area | Status |
|------|--------|
| Clinical scope boundary (DSMES only, no Rx/diagnosis) | Finalized |
| Risk escalation model (5 tiers, red-flag library) | Finalized |
| Knowledge corpus sources (2-tier retrieval model defined) | Finalized — all 10 PDFs downloaded |
| RAG chunking strategy and retrieval logic | Finalized — see `CHUNKING_LOGIC.md` |
| Conversational architecture (3 literacy registers, MI scaffolds) | Finalized |
| Kerala nutrition knowledge layer | Detailed — 15 clinical placeholders remain |
| Tech stack | **Fully decided** — all locked, see B1 |
| PDF ingestion parsers (`ingestion/parsers/`) | **Built** — all 10 corpus sources covered |
| Corpus extractors (`ingestion/extractors/`) | **Built + run** — all 10 parsed markdown files in `parsed/` |
| Chunking pipeline (`ingestion/chunkers/`) | **Built + run** — 4,059 chunks in `data/chunks/`; 0 chunks over 512 tok |
| Embedder + pgvector upsert (`ingestion/embedder/`) | **Built** — run against Neon once `POSTGRES_URL` is in `.env` |
| Phase 1 Context Engine — all 6 design items | **Complete** — see `PHASE1_CONTEXT_ENGINE_SPEC.md` |
| Phase 2 RAG Pipeline — all 10 design items | **Complete** — see `PHASE2_RAG_PIPELINE_SPEC.md` |

### What is blocking full pipeline completion

| # | Blocker | Owner |
|---|---------|-------|
| B1 | Tech Stack — EMR integration only (all other decisions made) | Engineering |
| B2 | Clinical Sign-offs (Nutrition Placeholders) | Dr. Rakesh K R + RD |
| B3 | Drug Education Content | Clinical Lead |
| B4 | RMP Loop Design | Preventify Operations |
| B5 | SaMD Regulatory Pathway | Compliance |
| B6 | Operations & Clinic Handoff | Preventify Operations |

### Immediate next engineering step

Build **`engine/phase1.py`** — the Phase 1 orchestrator:

1. Call `run_phase1(message, session_turns, user_id)` → Phase 1 output
2. Call `write_profile_signals(user_id, phase1_output["profile_signals"], conn)` → update patient profile in DB
3. Call `build_phase2_query(message, session_turns, profile, phase1_output["mid_clarification_resolved"])` → enriched query string
4. Call `run_phase2(...)` when `context_sufficient=True` and intent != `"escalation_only"`
5. Call `build_response(phase1_output, risk_tier, tier_3_subtype)` → merge risk nudge into response
6. Return structured turn result: phase1 + phase2 outputs + risk tier + final response text

After that, run the **embedder** against Neon (add `POSTGRES_URL` to `.env` first):
```
python ingestion/embedder/run.py
```
Then run `schemas/users_table.sql` AND `schemas/conversation_audit_log.sql` against the same Neon instance.

**Note:** `base_model_spec.md` is superseded. `BOT_CONVERSATION_ARCHITECTURE.md` is the authoritative architecture document.

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
    → Gemini 2.5 Pro generates English response
    → English → Malayalam translation
    → Patient receives Malayalam response
    → Clinic referral when indicated → Sugar Care Clinics
```

**Language layer is upstream of RAG:** The entire RAG pipeline operates in English. Malayalam is handled at the edges only — ASR + translation on input, translation on output. `bge-large-en-v1.5` (English-only) is correct; no multilingual model needed.

### PDF Extraction — One-Time Run Pattern

Parsers run **once per source PDF**. Output written to `parsed/<SOURCE>.json` — these are the artifacts the chunker and embedder consume. No continuous re-parsing.

```
python extract_corpus.py RSSDI_2022    # single source
python extract_corpus.py               # all sources
```

`parsed/` is gitignored. To re-run any extractor:
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

ICMR-NIN, IDF-DAR, and KDIGO use pdfplumber (Docling crashes with `std::bad_alloc` on their dense PDFs). All others use Docling.

---

### RAG System Design

**Chunking pipeline is built.** See `CHUNKING_LOGIC.md` for spec, `CHUNKING_DISCUSSION.md` for rationale. Output: `data/chunks/*.jsonl`.

```
python ingestion/chunkers/run.py               # all sources
python ingestion/chunkers/run.py RSSDI_2022    # single source
python ingestion/chunkers/run.py --dry-run     # count only
```

**Chunk counts:** RSSDI_2022 764 | ADA_2026 658 | KDIGO 891 | IDF_DAR 1,103 | ESC_2023 419 | ICMR_STW 10 | ICMR_NIN 71 | Anoop_Misra 58 | WHO_HEARTS 20 | Telemedicine 65 | **Total 4,059**

**Chunk metadata schema** — 14 fields (full spec in `CHUNKING_LOGIC.md`):
```json
{
  "chunk_id": "a3f2b89c1d4e5f67",
  "source": "RSSDI_2022",
  "year": 2022,
  "section_title": "Glycemic Targets",
  "text": "[RSSDI 2022 — Glycemic Targets]\n\nHbA1c target...",
  "retrieval_tier": "core",
  "condition_trigger": null,
  "india_specific": true,
  "kerala_food": false,
  "safety_critical": false,
  "grade_priority": 1,
  "meal_context": null,
  "text_hash": "sha256[:32]",
  "token_estimate": 87
}
```

**`retrieval_tier`:** `core` | `triggered` | `compliance`  
**`condition_trigger`:** `null` | `ckd` | `cardio` | `ramadan` | `hypertension`  
**`india_specific`:** `true` = RSSDI/ICMR/ICMR-NIN; `false` = ADA/ESC/KDIGO/WHO  
**`grade_priority`:** 1 (strongest) → 5 (contraindicated); ESC Class-III chunks get priority 5 + `safety_critical=True`

**Retrieval logic** (hard rule):

*Tier 1 — every turn:*
1. **RSSDI 2022 / ICMR STW 2024** — India-specific, checked first
2. **ADA 2026** — fallback; critical for elderly protocols (S12), CGM, hypoglycemia; re-ingest annually
3. **ICMR-NIN / Anoop Misra** — always active for any nutrition or food query

*Tier 2 — triggered only:*
- **KDIGO 2022** — CKD flag; overrides all Tier 1 on CKD queries
- **IDF-DAR** — Ramadan flag
- **ESC 2023** — Cardio flag
- **WHO HEARTS** — Hypertension flag (alongside ESC for BP-primary queries)

**Embedder pipeline** — `ingestion/embedder/`:

```
python ingestion/embedder/run.py                  # embed all 10 sources
python ingestion/embedder/run.py RSSDI_2022       # single source
python ingestion/embedder/run.py --dry-run        # count only
python ingestion/embedder/run.py --retry-failed   # retry logs/embed_failures.jsonl
```

- Connection: `DATABASE_URL` in `.env` (never committed); loaded via `python-dotenv`
- Batch size: 32 chunks; safe for CPU, ~2–4 GB RAM
- Single-source re-run: deletes all rows for that source, then re-inserts fresh (correct for annual updates)
- Failed chunks logged to `logs/embed_failures.jsonl` with full chunk JSON + error; `--retry-failed` upserts by `chunk_id`
- DB schema: `schemas/` — pgvector table `preventify_corpus` on Neon (PostgreSQL)
- Dependencies: `sentence-transformers`, `psycopg2-binary`, `pgvector`, `python-dotenv`, `tqdm`

**Two-stage retrieval (both required):**
- Stage 1 — `BAAI/bge-large-en-v1.5`: ANN search over 4,059 vectors → top-20 candidates
- Stage 2 — `BAAI/bge-reranker-large` (cross-encoder, self-hosted): re-scores each (query, chunk) pair → top-5 to Gemini

Using the same BAAI family for both avoids distribution mismatch.

**Namespacing:** Only Telemedicine Guidelines go into the `compliance` namespace. DPDP Rules 2025 and DSMES 2022 live in `reference/` only — **never ingest**.

### Knowledge Corpus

All corpus files in `corpus/`. See `corpus/README.md` for download status and update cadence.

**Tier 1 — Core (always active):**

| Folder | Document | Role |
|--------|----------|------|
| `RSSDI_2022/` | RSSDI CPR for T2DM 2022 | Primary — first-choice for standard T2DM queries |
| `ICMR_STW_2024/` | ICMR STW for T2DM 2024 | Primary — current GoI clinical decision flow |
| `ADA_2026/` | ADA Standards of Care 2026 (S01–S15) | Fallback; re-ingest annually each January |
| `ICMR_NIN/` | ICMR-NIN Food Composition Tables (IFCT 2017) | Nutrition anchor — carb/GI values for Indian/Kerala foods |
| `Anoop_Misra_South_Asian_Nutrition/` | Consensus Dietary Guidelines for Asian Indians 2011 | South Asian body baseline |

**Tier 2 — Condition-triggered:**

| Folder | Document | Trigger |
|--------|----------|---------|
| `KDIGO_2022_DM_CKD/` | KDIGO 2022 DM in CKD | CKD flag — overrides Tier 1 |
| `IDF_DAR/` | IDF-DAR Diabetes and Ramadan 2021 | Ramadan flag |
| `ESC_2023_CV_DM/` | ESC 2023 CVD in Diabetes | Cardio flag |
| `WHO_HEARTS/` | WHO HEARTS Technical Package | Hypertension flag |

`Kerala_NCD_Aardram/` moved to `reference/` — prescribing workflow for PHC doctors, not patient education; use during B2 sign-off only, do not ingest.

**Compliance namespace:**

| File | Document |
|------|----------|
| `Telemedicine_Practice_Guidelines_India_2020.pdf` | Telemedicine Practice Guidelines India 2020 |

### Risk Escalation Tiers

| Tier | Description | Action |
|------|-------------|--------|
| 0 | Education only | Continue conversation |
| 1 | Low concern | Nudge to next scheduled clinic visit |
| 2 | Moderate concern | Clinic within 1–2 weeks |
| 3 | High concern | Clinic within 24–48 hours |
| 4 | Same-day emergency | Patient safety instructions + immediate RMP notification |

Risk scoring runs silently in the background on every turn.

### Conversational Intelligence

- **Three literacy registers:** low / mid / high — switch dynamically based on patient cues
- **Motivational Interviewing:** OARS principles, stages of change
- **Family as clinical unit:** family members enrollable with patient consent
- **Faith/fasting handling:** Ramadan (IDF-DAR stratification), Ekadashi, Lent, Navratri — with clinical guardrails

### Lead Capture Architecture (v0.3)

Full spec: `preventify lead architecture v2.pdf`. Runs silently alongside the clinical layer — never felt as a sales flow.

**Build principles:** Safety first (clinical escalation always overrides); lean token footprint; single Neon PostgreSQL for vectors + user memory + lead data.

**Agentic decision loop — every turn:**
```
Receive message
→ Recall user memory (identity + clinical profile + lifetime score)
→ Classify: assign QDS (1–5) via LLM
→ Route: answer from RAG / escalate clinically / trigger consent / update lead
→ Respond + update memory and lifetime score
```

**QDS (Question Depth Score):** 1 = general awareness → 5 = complex/distressed. Assigned by LLM, not keyword matching. Dr. Rakesh must validate on 50 real questions before go-live.

**Capture trigger:** lifetime score ≥ 8 AND at least one QDS 3+ question AND ≥ 3 total messages. Clinical escalation questions are never routed into capture.

**User memory — 3 layers:** identity (UUID/WhatsApp), clinical profile (diabetes type, complications, meds), engagement (lifetime score with decay, consent status, lead status).

---

## Kerala-Specific Knowledge Layer

- **Rice:** matta and white parboiled; portion anchors in **ladles** (not cups)
- **Staples:** kappa, nendran banana, jackfruit, puttu, idli, dosa, appam — each with carb estimates
- **Coconut fat:** nuanced guidance, never blanket prohibition; clinical position pending (B2)
- **Fish:** mathi, ayala, karimeen, netholi — actively encouraged as cardiometabolic assets
- **Chaaya (sweet tea):** single highest-yield dietary intervention — 4–8 cups/day with sugar is often the largest hidden sugar source
- **Festivals:** Onam sadhya, Vishu, Christmas, Eid, Bakrid — each has a specific eating strategy
- **Ramadan:** IDF-DAR stratification, suhoor/iftar guidance, SMBG thresholds; verbatim vs adapted adoption pending (B2)
- **Monsoon protocols:** foot care escalation, indoor activity alternatives, insulin storage during power cuts
- **Gulf-migrant context:** remote family stakeholders, solo-living elderly patients

---

## Build Blockers

Each blocker is self-contained and can be picked up independently in a new chat.

### B1 — Tech Stack Decisions
**Owner:** Engineering | **Status:** One item open (EMR integration)

| Decision | Status | Choice |
|----------|--------|--------|
| LLM — Phase 1 | Decided | `gemini-2.0-flash-001` via Google AI Python SDK |
| LLM — Phase 2 | Decided | `gemini-2.5-pro-preview-06-05` via Google AI Python SDK |
| LLM — Memory compressor | Decided | `gemini-2.0-flash-001` |
| Context caching | Decided | Gemini context caching, 10-min TTL, ≥1,024 tokens |
| Embedding model | Decided | `BAAI/bge-large-en-v1.5` |
| Reranker | Decided | `BAAI/bge-reranker-large` (self-hosted, top-20 → top-5) |
| Vector store | Decided | pgvector on Neon — single DB for vectors, memory, leads |
| PDF parsing | Decided + built | Custom parser per source in `ingestion/parsers/` |
| Risk scoring engine | Decided | Hard-coded rule engine, deterministic, no ML, <500ms |
| ASR strategy | Open (post base model) | Whisper fine-tuned on Malayalam vs Google STT |
| **EMR integration** | **Open** | API contract, auth, patient record sync frequency |

### B2 — Clinical Sign-offs (Nutrition Placeholders)
**Owner:** Dr. Rakesh K R + RD | ~15 placeholders in `reference/kerala_nutrition_annex_v0_1.docx` — rice portions, GI values, coconut position, Ramadan stratification, breakfast carb estimates.

### B3 — Drug Education Content
**Owner:** Clinical Lead | Per-drug-class educator-level monographs needed for Metformin, Sulfonylureas, DPP-4i, SGLT2i, GLP-1 RA, Basal insulin, Premixed insulin.

### B4 — RMP Loop Design
**Owner:** Preventify Operations | Named RMP list, escalation SLA, review SOP, notification channel.

### B5 — SaMD Regulatory Pathway
**Owner:** Compliance | CDSCO Class B confirmation, MDR 2017 registration, DPDP 2023 data architecture, consent versioning, conversation logging compliance.

### B6 — Operations & Clinic Handoff
**Owner:** Preventify Operations | Clinic capacity, referral handoff SOP, patient cohort definitions, acquisition funnel.

---

## Regulatory Context

- **Classification:** SaMD Class B, CDSCO India (anticipated — formal confirmation pending, B5)
- **Data protection:** DPDP Act 2023 — consent must be versioned, data must have a deletion path, breach protocol required
- **RMP-in-the-loop:** Named RMP required for audit, escalation receipt, and content review (design pending, B4)
- **Conversation logging:** Required for audit; retention must be DPDP-compliant
