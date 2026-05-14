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

## Current Project Stage (as of 2026-05-13)

**Stage: Build — Ingestion Pipeline in Progress**

Specification phase is complete. Engineering has started. The base model spec (`base_model_spec.md`) is the authoritative engineering reference. The next milestone is a working end-to-end English query → RAG response pipeline.

### What is locked and ready

| Area | Status |
|------|--------|
| Clinical scope boundary (DSMES only, no Rx/diagnosis) | Finalized |
| Risk escalation model (5 tiers, red-flag library) | Finalized |
| Knowledge corpus sources (2-tier retrieval model defined) | Finalized — all 10 PDFs downloaded; Tier 1 core: 5/5 complete; Tier 2 condition-triggered: 4/4 complete |
| RAG chunking strategy and retrieval logic | Finalized — Tier 1 always-active (RSSDI/ICMR first → ADA fallback); Tier 2 condition-triggered (KDIGO on CKD, IDF-DAR on Ramadan, ESC on cardio, etc.) |
| Conversational architecture (3 literacy registers, MI scaffolds) | Finalized |
| Kerala nutrition knowledge layer (food-by-food, festivals, fasting, monsoon) | Detailed — 15 clinical placeholders remain |
| Compliance hooks (DPDP, Telemedicine Guidelines, SaMD Class B posture) | Framed — not yet filed |
| Tech stack — LLM, embedding, reranker, vector DB, PDF parsing | **Decided and partially implemented** — see B1 below |
| PDF ingestion parsers (`ingestion/parsers/`) | **Built** — custom parser per source, all 10 corpus sources covered |

### Engineering work completed

| Module | Location | Status |
|--------|----------|--------|
| Config + settings | `config/settings.py`, `config/corpus_manifest.json` | Done |
| Docker services (Qdrant + Postgres) | `docker-compose.yml` | Done |
| PDF parsers — all 10 corpus sources | `ingestion/parsers/` | Done |
| Base model specification | `base_model_spec.md` | Done |
| Corpus extraction runner | `extract_corpus.py` | Done — run once per source, output lives in `parsed/` |

### What is blocking full pipeline completion

| # | Blocker | Owner |
|---|---------|-------|
| B1 | Tech Stack Decisions — EMR integration only (all other decisions made) | Engineering |
| B2 | Clinical Sign-offs (Nutrition Placeholders) | Dr. Rakesh K R + RD |
| B3 | Drug Education Content | Clinical Lead |
| B4 | RMP Loop Design | Preventify Operations |
| B5 | SaMD Regulatory Pathway | Compliance |
| B6 | Operations & Clinic Handoff | Preventify Operations |

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
    → ASR (code-mixed Malayalam-English)
    → RAG retrieval (clinical corpus)
    → Risk scoring (5-tier escalation)
    → Response generation (literacy-register adapted)
    → Clinic referral when indicated → Sugar Care Clinics
```

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

Three sources use Docling-based extractors — for two-column layouts, complex tables, or rotated headers that pdfplumber garbles. Two sources use pdfplumber-based extractors (faster, better suited to their structure).

| Extractor script | Backend | Source PDF | Output |
|-----------------|---------|-----------|--------|
| `ingestion/extractors/tier1/ada.py` | Docling | All 15 `ADA_2026_S*.pdf` in `corpus/tier1_clinical/ADA_2026/` | `parsed/ADA_2026_docling.md` |
| `ingestion/extractors/tier1/anoop_misra.py` | Docling | `corpus/tier1_clinical/Anoop_Misra_South_Asian_Nutrition/Anoop_Misra_Consensus_Dietary_Guidelines_Asian_Indians_2011.pdf` | `parsed/Anoop_Misra_docling.md` |
| `ingestion/extractors/tier1/icmr_stw.py` | Docling | `corpus/tier1_clinical/ICMR_STW_2024/ICMR_STW_Diabetes_T2DM_2024.pdf` | `parsed/ICMR_STW_2024_docling.md` |
| `ingestion/extractors/tier1/icmr_nin.py` | pdfplumber | `corpus/tier1_clinical/ICMR_NIN/ICMR_NIN_Indian_Food_Composition_Tables.pdf` | `parsed/ICMR_NIN_docling.md` |
| `ingestion/extractors/tier1/rssdi.py` | pdfplumber | `corpus/tier1_clinical/RSSDI_2022/RSSDI_Clinical_Practice_Recommendations_T2DM_2022.pdf` | `parsed/RSSDI_2022_docling.md` |

**Why ICMR-NIN uses pdfplumber:** The IFCT PDF is 585 pages of fixed-column composition tables. Docling's VLM renderer crashes with `std::bad_alloc` on this PDF (too many dense-table pages for CPU RAM). The existing `ICMRNINParser` in `ingestion/parsers/food_table.py` was built specifically for IFCT's layout (fixed x-position column detection) and produces clean structured output. `extract_icmr_nin_docling.py` is a thin wrapper that runs it and emits grouped markdown tables with a RAG header.

**To re-run any extractor:**
```
python ingestion/extractors/tier1/ada.py
python ingestion/extractors/tier1/anoop_misra.py
python ingestion/extractors/tier1/icmr_stw.py
python ingestion/extractors/tier1/icmr_nin.py
python ingestion/extractors/tier1/rssdi.py
```

**What every Docling extractor does (the shared pattern):**

1. `DocumentConverter().convert(pdf_path)` — Docling VLM parse
2. `html.unescape(doc.export_to_markdown())` — full document markdown; `html.unescape` restores `<`/`>` operators that Docling HTML-encodes as `&lt;`/`&gt;`
3. Table replacement — regex finds every markdown table block in the output, replaces it with a grid-rendered version built from `doc.tables[i].data.grid`:
   - Tracks `id(cell)` to emit spanning cells only once (no repeated content across rows)
   - Collapses footnote rows where all non-empty cells are identical text
   - `_clean_cell()` runs `html.unescape` + normalises whitespace + converts `\n` → ` · ` for multi-line cells
4. Writes final markdown to `parsed/`

**Anoop Misra extras (source-specific):**
- `_restore_comparison_operators()` — replaces `\x15` (U+0015, how the 2011 PDF font encodes `≥`) with the correct symbol
- `_inject_section_metadata()` — inserts `<!-- rag_metadata ... -->` comments after substantive headings (generic headings like "Recommendations", "References", "Appendix" are skipped via `_SKIP_METADATA_SECTIONS` blocklist)
- RAG document header with citation, population, topic tags
- Indian food glossary appended (35 Hindi/regional food terms mapped to English)

**ADA extras (source-specific):**
- Loops over all 15 section PDFs in order; inserts a `<!-- source: ADA_2026 | file: ADA_2026_Sxx.pdf | citation: ... -->` separator between sections in the merged output

**ICMR STW 2024 extras (source-specific):**
- `_inject_section_metadata()` — inserts `<!-- rag_metadata ... -->` comments after substantive headings with clinical topic tags (treatment_algorithm, drug_escalation, HbA1c_targets, etc.)
- `_annotate_algorithm_steps()` — detects "Step N" lines (treatment escalation steps) and prepends a rag_metadata comment so chunks covering algorithm steps rank higher on clinical workflow queries
- RAG document header with GoI-context note (reflects what PHC/Aardram facilities actually stock and prescribe)

**ICMR-NIN extras (source-specific) — pdfplumber backend:**
- Uses `ICMRNINParser` from `ingestion/parsers/food_table.py` (fixed x-position column detection for IFCT's layout)
- Groups 7,000+ food rows by IFCT food group letter (A=Cereals, B=Legumes, C=Vegetables, K=Marine Fish, etc.) and renders each group as a markdown table
- Each group heading gets a `<!-- rag_metadata ... -->` comment with food group topic tag
- Kerala food metadata (food-row level tags) is deferred to chunking time — not applied at extraction

**RSSDI 2022 extras (source-specific):**
- `_inject_section_metadata()` — inserts `<!-- rag_metadata ... -->` comments after substantive headings covering glycemic targets, drug classes, complication screening, comorbidities, and special populations
- `_annotate_evidence_grades()` — detects inline RSSDI grade markers `(A)`, `(B)`, `(C)`, `(E)` on recommendation lines (>40 chars) and prepends a rag_metadata comment with `evidence_grade` field; enables grade-filtered retrieval to surface Grade A recommendations preferentially for safety-critical queries
- RAG document header with primary-source priority note (RSSDI first over ADA for all India queries)

**To create a new Docling extractor for another source**, copy either script and change: `PDF_PATH`, `OUT_FILE`, `SOURCE_KEY`, `CITATION`, `YEAR`. Keep the `_clean_cell` / `_render_table_grid` / `_MD_TABLE_RE` / `html.unescape` core unchanged — that part is proven and shared.

---

### RAG System Design

**Chunking:** By clinical recommendation unit — not by token count. Each chunk must carry structured metadata:

```json
{
  "source": "RSSDI_2022",
  "year": 2022,
  "section_ref": "S5.2",
  "evidence_grade": "A",
  "population_scope": ["T2DM"],
  "age_scope": "adult",
  "topic_tags": ["medication", "metformin"],
  "retrieval_tier": "core",
  "condition_trigger": null,
  "india_specific": true
}
```

**`retrieval_tier` values:** `core` (Tier 1 — every turn) | `triggered` (Tier 2 — condition flag only) | `compliance`  
**`condition_trigger` values:** `null` for Tier 1 | `ckd` | `cardio` | `ramadan` | `hypertension`  
**`india_specific`:** `true` = RSSDI/ICMR/ICMR-NIN/IDF-DAR; `false` = ADA/ESC/KDIGO/WHO (global, used as fallback or specialist override)

> **No geography_tag.** All patients are Kerala-based Malayalam speakers. Geography is not a retrieval dimension — it only creates noise. `india_specific` captures the only meaningful distinction: whether a source was calibrated for Indian physiology or is a global guideline used as fallback.

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

**Reranker:** Apply on top-20 candidates (bge-reranker-large or Cohere Rerank). This is a meaningful quality lever — do not skip.

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
| Vector store | **Decided** | Qdrant (self-hosted via Docker) |
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
