# Preventify Diabetes Educator AI — Knowledge Corpus

Organised by the three-tier sourcing plan in `reference/guideline_corpus_sources.docx`.

---

## Folder Structure

```
corpus/
├── tier1_clinical/          # Patient-facing RAG namespace
├── tier2_regulatory/        # Compliance namespace only — never surface in patient answers
├── tier3_supplementary/     # Supplementary depth
└── README.md                # This file
```

---

## Tier 1 — Core Clinical (Patient-Facing RAG)

| Folder | Document | Status | Size | Source URL |
|--------|----------|--------|------|------------|
| `ADA_2026/` | ADA Standards of Care in Diabetes 2026 | **Manual download required** | — | https://diabetesjournals.org/care/issue/49/Supplement_1 (open access, 17 chapters) |
| `RSSDI_2022/` | RSSDI Clinical Practice Recommendations for T2DM 2022 | Downloaded | 2.2 MB | https://www.rssdi.in/newwebsite/RSSDI-Clinical-Practice-Recommendations-2022%20(1).pdf |
| `RSSDI_2017/` | RSSDI Clinical Practice Recommendations for T2DM 2017 | Downloaded | 3.6 MB | https://www.rssdi.in/newwebsite/pdfdata/rssdiGuidelines/2017/RSSDI%20clinical%20practice%20recommendations%20for%20the%20management%20of%20type%202%20DM-2017.pdf |
| `ICMR_2018/` | ICMR Guidelines for Management of Type 2 Diabetes 2018 | Downloaded | 9.8 MB | https://www.icmr.gov.in/icmrobject/custom_data/pdf/resource-guidelines/ICMR_GuidelinesType2diabetes2018_0.pdf |
| `ICMR_STW_2024/` | ICMR Standard Treatment Workflow: T2DM 2024 | Downloaded | 325 KB | https://www.icmr.gov.in/icmrobject/uploads/STWs/1726567245_diabetes_mellitus_type_2.pdf |
| `ADA_ADCES_DSMES_2022/` | 2022 National Standards for DSMES | **Manual download required** | — | https://diabetesjournals.org/care/article/45/2/484/140905/2022-National-Standards-for-Diabetes-Self (open access) |
| `KDIGO_2022_DM_CKD/` | KDIGO 2022 Guideline for Diabetes Management in CKD | Downloaded | 7.9 MB | https://kdigo.org/wp-content/uploads/2022/10/KDIGO-2022-Clinical-Practice-Guideline-for-Diabetes-Management-in-CKD.pdf |
| `KDIGO_2024_CKD/` | KDIGO 2024 Clinical Practice Guideline for CKD | Downloaded | 5.7 MB | https://kdigo.org/wp-content/uploads/2024/03/KDIGO-2024-CKD-Guideline.pdf |
| `IDF_Atlas_2025/` | IDF Diabetes Atlas 11th Edition 2025 | **Manual download required** | — | https://diabetesatlas.org/resources/idf-diabetes-atlas-2025/ (requires form registration) |

**Manual download note — ADA 2026:** The Standards of Care is split into 17 supplement chapters. Download each from the issue page and save as `ADA_2026_S01.pdf` through `ADA_2026_S17.pdf`. Re-ingest annually each January.

**Manual download note — IDF Atlas 2025:** The IDF blocks hotlinking. Visit the URL above, complete the short registration form, and save the downloaded file as `IDF_Diabetes_Atlas_11th_Edition_2025.pdf` in this folder.

---

## Tier 2 — India Regulatory (Compliance Namespace Only)

> These documents must **never** surface in patient-facing answers. Ingest into a separate `compliance` namespace in the vector store.

| Folder | Document | Status | Size | Source URL |
|--------|----------|--------|------|------------|
| `Telemedicine_2020/` | Telemedicine Practice Guidelines India 2020 | Downloaded | 1.4 MB | https://esanjeevani.mohfw.gov.in/assets/guidelines/Telemedicine_Practice_Guidelines.pdf |
| `DPDP_2023/` | Digital Personal Data Protection Act 2023 | Downloaded | 178 KB | https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf |
| `DPDP_Rules_2025/` | Digital Personal Data Protection Rules 2025 | Downloaded | 112 KB | https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/jan/doc202515481101.pdf |
| `DIPSI_GDM_2018/` | National Guidelines for GDM (DIPSI-aligned) 2018 | Downloaded | 936 KB | https://icogonline.org/wp-content/uploads/pdf/gcpr/gdm-dipsi-guidline.pdf |

---

## Tier 3 — Supplementary Depth

| Folder | Document | Status | Notes |
|--------|----------|--------|-------|
| `Anoop_Misra_South_Asian_Nutrition/` | South Asian Nutrition Consensus (Anoop Misra et al.) | **Manual — PubMed search** | Search PubMed: "consensus dietary guidelines healthy living prevention obesity metabolic syndrome diabetes Asian Indians Misra" |
| `WHO_HEARTS/` | WHO HEARTS Technical Package | **Manual download** | https://www.who.int/teams/noncommunicable-diseases/hearts — download the technical package PDF |
| `ESC_2023_CV_DM/` | ESC 2023 Guidelines on CV Disease in Diabetes | **Paywalled** | European Heart Journal — requires institutional access; pocket version at escardio.org |
| `IDF_DAR/` | IDF-DAR Practical Guidelines — Diabetes and Ramadan | **Manual download** | https://www.idf.org/our-activities/care-prevention/diabetes-and-ramadan.html |
| `Kerala_NCD_Aardram/` | Kerala State NCD Cell / Aardram Protocols | **Institutional request** | Not publicly hosted — request directly from Kerala State Health Department |
| `ICMR_NIN/` | ICMR-NIN Food Composition Tables | **Manual download** | https://www.nin.res.in — Indian Food Composition Tables; used for validating Kerala food carb estimates |
| `ADCES_Curriculum/` | ADCES Diabetes Education Curriculum | **Requires license** | Commercial product — contact ADCES for licensing |

---

## Ingestion Status Summary

| Tier | Total | Downloaded | Manual Required |
|------|-------|-----------|-----------------|
| Tier 1 Clinical | 9 | 6 | 3 (ADA 2026, ADA/ADCES DSMES 2022, IDF Atlas 2025) |
| Tier 2 Regulatory | 4 | 4 | 0 |
| Tier 3 Supplementary | 7 | 0 | 7 |
| **Total** | **20** | **10** | **10** |

---

## RAG Metadata Schema

Each ingested chunk must carry this metadata:

```json
{
  "source": "RSSDI_2022",
  "year": 2022,
  "section_ref": "S5.2",
  "evidence_grade": "A",
  "population_scope": ["T2DM"],
  "age_scope": "adult",
  "topic_tags": ["medication", "metformin"],
  "geography_tag": "India",
  "tier": 1,
  "namespace": "clinical"
}
```

`geography_tag` values: `global` | `India` | `South-Asia` | `Kerala`  
`namespace` values: `clinical` (Tier 1 + 3) | `compliance` (Tier 2)

---

## Update Cadence

| Source | Frequency | Next Action |
|--------|-----------|-------------|
| ADA Standards | Annual (January) | Re-ingest each January |
| RSSDI | Every 2–3 years | Monitor RSSDI website |
| KDIGO DM-CKD | Watch for 2026 update | Check kdigo.org |
| DPDP Rules | Phased enforcement — track MeitY quarterly | Enforcement timeline: 12–18 months from Nov 2025 |
| Telemedicine Guidelines | Periodic MoHFW amendments | Monitor mohfw.gov.in notifications |
| Kerala State Protocols | No fixed schedule | Institutional contact required |
