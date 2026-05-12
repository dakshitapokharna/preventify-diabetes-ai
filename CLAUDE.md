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

Always read these before making decisions about clinical scope, data sourcing, or patient-facing behavior.

---

## Current Project Stage (as of 2026-05-12)

**Stage: Pre-Build — Specification Phase v0.1**

No code has been written. The three reference documents are working drafts designed to reach full alignment before engineering kickoff. The specification phase is nearing completion; the next milestone is **v1 sign-off**, after which build begins.

### What is locked and ready

| Area | Status |
|------|--------|
| Clinical scope boundary (DSMES only, no Rx/diagnosis) | Finalized |
| Risk escalation model (5 tiers, red-flag library) | Finalized |
| Knowledge corpus sources (Tier 1–3 identified, ingestion strategy defined) | Finalized — 10 PDFs downloaded to `corpus/`; 3 Tier 1 + all Tier 3 still need manual/licensed acquisition |
| RAG chunking strategy and retrieval logic | Finalized |
| Conversational architecture (3 literacy registers, MI scaffolds) | Finalized |
| Kerala nutrition knowledge layer (food-by-food, festivals, fasting, monsoon) | Detailed — 15 clinical placeholders remain |
| Compliance hooks (DPDP, Telemedicine Guidelines, SaMD Class B posture) | Framed — not yet filed |

### What is blocking build kickoff

Six independent blocker tracks are defined below. Each can be opened as a standalone work session. See the **Build Blockers** section for full detail.

| # | Blocker | Owner |
|---|---------|-------|
| B1 | Tech Stack Decisions | Engineering |
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

### RAG System Design

**Chunking:** By clinical recommendation unit — not by token count. Each chunk must carry structured metadata:

```json
{
  "source": "ADA_2026",
  "year": 2026,
  "section_ref": "S5.2",
  "evidence_grade": "A",
  "population_scope": ["T2DM"],
  "age_scope": "adult",
  "topic_tags": ["medication", "metformin"],
  "geography_tag": "global"   // global | India | South-Asia | Kerala
}
```

**Retrieval priority:** population type filter → India-specific preference over global → recency.

**Reranker:** Apply on top-20 candidates (bge-reranker-large or Cohere Rerank). This is a meaningful quality lever — do not skip.

**Namespacing:** Regulatory/compliance documents (Telemedicine Guidelines, DPDP Act, etc.) go into a separate `compliance` namespace and must **never** surface in patient-facing answers.

### Knowledge Corpus (3 Tiers)

All corpus files live in `corpus/`. See `corpus/README.md` for full download status, source URLs, and update cadence.

**Tier 1 — Core clinical (patient-facing RAG) — `corpus/tier1_clinical/`:**

| Folder | Document | File Status |
|--------|----------|-------------|
| `ADA_2026/` | ADA Standards of Care in Diabetes 2026 | **Manual download needed** — 17 open-access chapters at diabetesjournals.org/care/issue/49/Supplement_1; re-ingest annually each January |
| `RSSDI_2022/` | RSSDI Clinical Practice Recommendations for T2DM 2022 | Downloaded |
| `RSSDI_2017/` | RSSDI Clinical Practice Recommendations for T2DM 2017 | Downloaded (supplementary depth) |
| `ICMR_2018/` | ICMR Guidelines for Management of T2DM 2018 | Downloaded |
| `ICMR_STW_2024/` | ICMR Standard Treatment Workflow for T2DM 2024 | Downloaded |
| `ADA_ADCES_DSMES_2022/` | ADA/ADCES 2022 National Standards for DSMES | **Manual download needed** — open access at diabetesjournals.org/care/article/45/2/484 |
| `KDIGO_2022_DM_CKD/` | KDIGO 2022 Guideline for Diabetes Management in CKD | Downloaded |
| `KDIGO_2024_CKD/` | KDIGO 2024 CKD Guideline | Downloaded |
| `IDF_Atlas_2025/` | IDF Diabetes Atlas 11th Edition 2025 (epidemiology only — never surface for treatment queries) | **Manual download needed** — requires form registration at diabetesatlas.org/resources/idf-diabetes-atlas-2025/ |

**Tier 2 — India regulatory (`compliance` namespace only) — `corpus/tier2_regulatory/`:**

| Folder | Document | File Status |
|--------|----------|-------------|
| `Telemedicine_2020/` | Telemedicine Practice Guidelines India 2020 | Downloaded |
| `DPDP_2023/` | Digital Personal Data Protection Act 2023 | Downloaded |
| `DPDP_Rules_2025/` | Digital Personal Data Protection Rules 2025 | Downloaded |
| `DIPSI_GDM_2018/` | MoHFW National Guidelines for GDM/DIPSI 2018 | Downloaded |

**Tier 3 — Supplementary — `corpus/tier3_supplementary/`:**

| Folder | Document | File Status |
|--------|----------|-------------|
| `Anoop_Misra_South_Asian_Nutrition/` | South Asian nutrition consensus (Anoop Misra et al.) | Manual — PubMed search required |
| `WHO_HEARTS/` | WHO HEARTS Technical Package | Manual download — who.int/teams/noncommunicable-diseases/hearts |
| `ESC_2023_CV_DM/` | ESC 2023 CV disease in diabetes guidelines | Paywalled — requires institutional access |
| `Kerala_NCD_Aardram/` | Kerala State NCD Cell / Aardram protocols | Institutional request — not publicly hosted |
| `IDF_DAR/` | IDF-DAR Practical Guidelines for Diabetes and Ramadan | Manual download — idf.org/our-activities/care-prevention/diabetes-and-ramadan.html |
| `ICMR_NIN/` | ICMR-NIN Food Composition Tables | Manual — nin.res.in; used for B2 nutrition placeholder validation |
| `ADCES_Curriculum/` | ADCES Diabetes Education Curriculum | Requires license — contact ADCES |

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
**Dependency:** None — can start immediately

Decisions required before any code is written:

1. **LLM selection** — which model(s) to use for response generation; fine-tuning strategy (if any); grounding architecture
2. **ASR strategy** — code-mixed Malayalam-English speech recognition; no off-the-shelf solution handles this well; evaluate Whisper fine-tuned on Malayalam vs. Google STT vs. custom model
3. **RAG pipeline tooling** — PDF ingestion and parsing (clinical PDFs have complex tables and footnotes); embedding model choice; vector store (Pinecone, Weaviate, pgvector, etc.); chunking implementation by recommendation unit (not token count)
4. **Risk scoring engine** — rule-based vs. ML model vs. hybrid; must run silently on every turn with <500ms latency target
5. **EMR integration** — how the bot connects to Sugar Care Clinics' patient records; API design, auth, data sync frequency

**Starting context for a new chat:** Read `reference/diabetes_educator_intelligence_v0_1.docx` and `reference/guideline_corpus_sources.docx`. The task is to evaluate options and produce a recommended tech stack document covering all five decisions above, with rationale and trade-offs for each.

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
