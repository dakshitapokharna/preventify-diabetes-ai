# Preventify Diabetes Educator AI — Project Summary

## What Has Been Built

| Module | State |
|--------|-------|
| Clinical scope and safety boundary definitions | Defined and documented |
| Base model specification | Written and signed off |
| Knowledge corpus: 10 guidelines across retrieval tiers | Downloaded and organised |
| PDF extraction: all 10 sources | Parsed and annotated |
| Chunking: all 10 sources | Built and run |
| Vector embedding: all 10 sources | Embedded into Qdrant |

---

## Hard Safety Boundaries

The system is permanently prohibited from:

- Recommending a specific drug dose or titration
- Substituting or stopping a patient's medication
- Making a diagnosis
- Interpreting lab results without clinical context
- Claiming to be a doctor or RMP

These are enforced at three independent points: system prompt, constraint check after reranking, and response post-processor.

---

## Tech Stack

| Role in architecture | Choice |
|----------------------|--------|
| LLM for response generation | Claude Sonnet 4.6 |
| Chunk and query encoding | BAAI/bge-m3 |
| Retrieval reranking (top-20 to top-5) | BAAI/bge-reranker-v2-m3 |
| Vector search and chunk storage | Qdrant (self-hosted) |
| Patient profiles, risk state, audit logs | Postgres 16 (self-hosted) |
| Risk flag evaluation on every turn | Hard-coded deterministic rule engine |
| Malayalam speech to text | Not yet selected |

---

## Knowledge Corpus

### Tier 1: Always active on every turn

| Guideline | Role |
|-----------|------|
| RSSDI Clinical Practice Recommendations for T2DM 2022 | First-choice for all standard diabetes queries, India-specific |
| ICMR Standard Treatment Workflow for T2DM 2024 | Most current Government of India clinical decision flow |
| ADA Standards of Care in Diabetes 2026 (15 sections) | Global fallback when India sources are silent; re-ingested every January |
| ICMR-NIN Indian Food Composition Tables (IFCT 2017, rev. 2024) | Authoritative carb and GI values for all Indian and Kerala foods |
| Consensus Dietary Guidelines for Asian Indians, Misra et al. 2011 | Corrects Western BMI and carb assumptions for South Asian patients |

### Tier 2: Condition-triggered

| Guideline | Trigger | Why triggered and not always-on |
|-----------|---------|----------------------------------|
| KDIGO 2022 Diabetes Management in CKD | CKD flag | Overrides Tier 1 on kidney queries; irrelevant for patients without CKD |
| IDF-DAR Practical Guidelines for Diabetes and Ramadan 2021 | Ramadan flag | Suhoor/Iftar-specific guidance is clinically dangerous if served outside that context |
| ESC 2023 Guidelines on CV Disease in Diabetes | Cardio flag | Overrides Tier 1 on cardiovascular queries |
| WHO HEARTS Technical Package | Hypertension flag | Triggered alongside ESC for BP-primary queries |

### Compliance namespace: never shown to patient

| Guideline | Purpose |
|-----------|---------|
| Telemedicine Practice Guidelines India 2020 | Enforces legal scope boundaries in real time; queried silently by internal system logic only |

### Team reference only: not ingested into the AI

| Document | Reason excluded |
|----------|----------------|
| Kerala NCD Aardram Treatment Protocol 2021 | Prescribing workflow for PHC doctors; contains drug titration instructions that would violate the no-dose boundary |
| DPDP Rules 2025 | Legal engineering reference for the team |
| ADA/ADCES DSMES 2022 National Standards | Design reference used to shape conversational scope; not a knowledge source |

---

## Chunking

The chunker runs on the parsed markdown output of all 10 extractors and produces structured JSON chunks ready for embedding. It is implemented in `ingestion/chunkers/` and has been run across all 10 sources.

**Clinical text and recommendations**

Each chunk is one clinical recommendation or one coherent clinical statement. The chunker splits at recommendation boundaries detected from the `rag_metadata` annotation comments embedded during extraction, not at arbitrary word or token counts. This means a chunk always carries a complete, citable clinical unit. Partial recommendations are never split across chunk boundaries.

Every chunk carries a structured metadata envelope:

- `source`: guideline identifier (e.g. RSSDI_2022, ADA_2026)
- `year`: publication year
- `section_ref`: section number where available
- `evidence_grade`: A / B / C / E where the guideline provides it
- `topic_tags`: clinical topic list (glycemic, foot, medication, nutrition, renal, etc.)
- `retrieval_tier`: core or triggered
- `condition_trigger`: null for Tier 1; ckd / ramadan / cardio / hypertension for Tier 2
- `india_specific`: true for RSSDI, ICMR, ICMR-NIN, Anoop Misra; false for ADA, ESC, KDIGO, WHO

Where a guideline marks recommendations with evidence grades (Grade A through E), the chunker reads the `evidence_grade` field from the annotation and writes it into the chunk metadata. The retrieval engine uses this at query time to prefer Grade A evidence for safety-critical queries over expert opinion (Grade E).

**Table content**

Small tables are stored as a single atomic chunk tagged `content_type: table`. When a table is too large for one chunk, the chunker splits it by row groups and repeats the full header row at the start of every fragment. The table title or caption is also prepended to every fragment so every chunk is self-contained: the column labels and table context travel with the data regardless of where the split falls.

Tables in the knowledge base include food composition values for 7,000+ Indian food items (ICMR-NIN), drug class dosing matrices (RSSDI, ICMR STW), cardiovascular risk charts (ESC, WHO HEARTS), and CKD eGFR thresholds (KDIGO). Splitting any of these without the header would produce fragments that appear authoritative but are clinically uninterpretable.

**Safety-critical content**

Content carrying the `safety_critical: true` annotation from extraction (blood glucose thresholds, Ramadan break-fast rules, blood pressure action points, CKD drug thresholds) is treated as zero-loss by the chunker. These chunks are never truncated or split across boundaries regardless of size. The full threshold value and its clinical context are always kept together in one chunk.

---

## Vector Embedding

All chunks from all 10 sources have been embedded and loaded into Qdrant. The index is live and queryable.

| Component | Choice |
|-----------|--------|
| Embedding model | BAAI/bge-m3 (multilingual, state-of-the-art on MTEB; supports Malayalam for the voice layer) |
| Vector database | Qdrant (self-hosted, Docker) |
| Reranker | BAAI/bge-reranker-v2-m3 (top-20 to top-5; best-in-class multilingual cross-encoder) |
| Namespaces | `clinical` (patient-facing) and `compliance` (internal only) |

**Embedding model choice**

`BAAI/bge-m3` is chosen over English-only alternatives because the system will add Malayalam in the voice layer. Using a multilingual model from the start means the vector index does not need to be rebuilt when the language layer is added. It is self-hosted, avoiding any dependency on an external embedding API for patient data.

**Two-namespace index**

The Qdrant collection is partitioned into two namespaces. The `clinical` namespace holds all Tier 1 and Tier 2 guideline chunks and is the only namespace that can appear in a patient-facing response. The `compliance` namespace holds the Telemedicine Guidelines chunks and is queried only by internal system logic for scope-boundary enforcement. No query path can retrieve a compliance chunk and return it to the patient.

**Reranker**

Vector similarity alone retrieves semantically close but clinically mismatched results on medical queries. The reranker takes the top-20 candidates from the vector search and reranks them by clinical relevance to the query, returning the top 5. This is the quality gate between retrieval and generation and is not optional. The model is self-hosted to keep all patient query data off external APIs.

**Metadata filtering**

Before vector search runs, a metadata pre-filter narrows the candidate pool using the chunk metadata written at chunking time. Population type, retrieval tier, condition trigger, india_specific flag, and evidence grade are all filterable without touching vector similarity. This reduces both latency and the chance of a clinically irrelevant chunk ranking highly on semantic similarity alone.

---

## Retrieval Logic

1. RSSDI and ICMR checked first for all standard queries
2. ADA used as fallback when India sources are silent
3. ICMR-NIN and Anoop Misra always active for nutrition queries
4. When a condition flag fires, the specialist guideline overrides all Tier 1 sources for that sub-query
5. Compliance namespace permanently isolated; never returned to the patient

The current retrieval logic is based on metadata filtering and semantic similarity. This is the foundation layer. Further optimisation work on latency, query routing, and ranking signals will be done once the end-to-end pipeline is running and measured.

---

## Kerala-Specific Knowledge Layer

| Area | What is decided |
|------|----------------|
| Rice portions | Measured in ladles (Kerala patients do not use cups) |
| Chaaya (sweet tea) | Identified as the highest-yield single dietary intervention; 4-8 cups with sugar daily is typically the largest hidden sugar source |
| Coconut fat | Nuanced guidance, never blanket prohibition; culturally non-negotiable; formal clinical position pending sign-off |
| Fish | Actively encouraged; local varieties (mathi, ayala, karimeen, netholi) are cardiometabolic assets |
| Festival eating | Onam sadhya, Vishu, Christmas, Eid, Bakrid; each has a specific management plan |
| Ramadan | IDF-DAR risk stratification (very high / high / moderate / low) with suhoor/iftar-specific guidance and break-fast thresholds |
| Monsoon | Foot care escalation protocols, indoor activity alternatives, insulin storage during power cuts |
| Gulf-migrant context | Remote family stakeholders and solo-living elderly patients explicitly supported |
