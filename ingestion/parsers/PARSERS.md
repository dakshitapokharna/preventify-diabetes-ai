# Corpus Parser Documentation

How each PDF is extracted and what the output looks like.

All parsers live in `ingestion/parsers/`. Output goes to `parsed/<SOURCE>.json`.
Run with: `python extract_corpus.py <SOURCE>`

---

## Output schema (all sources)

Every parser emits a list of **blocks**. Each block has:

```json
{
  "block_type": "recommendation | heading | narrative | table | food_row",
  "page_num": 12,
  "section": "nearest ancestor heading text",
  "text": "the extracted content as a plain string",
  "evidence_grade": "A | B | C | E | 1A | 2B | (empty string if none)"
}
```

`table` blocks additionally carry `table_data` (list of row dicts).  
`food_row` blocks additionally carry `food_data` (nutrient dict).

---

## 1. RSSDI 2022

**Parser class:** `RSSDirectParser` (`recommendation.py`)  
**PDF:** `corpus/tier1_clinical/RSSDI_2022/RSSDI_Clinical_Practice_Recommendations_T2DM_2022.pdf`  
**Page size:** 720×405 pt (landscape), single-column

### How it works

1. On each page, tables are extracted first (pdfplumber `find_tables()`), stored as `table` blocks.
2. Remaining text is read line by line.
3. A line in ALL CAPS with no bullet → `heading`, updates `current_section`.
4. A line starting with a bullet character (`●`, `•`, `◆`, `▶`, `-`, `–`) → `recommendation`. Evidence grade `(A)` / `(B)` / `(C)` / `(E)` is stripped from the end and stored in `evidence_grade`.
5. Everything else accumulates into a `pending` buffer and is flushed as `narrative` when the next heading or bullet is encountered.

### Block types produced

| Type | Count | Trigger |
|------|-------|---------|
| `heading` | ~220 | ALL CAPS line, no bullet |
| `recommendation` | ~728 | Line starts with `● • ◆ ▶ - –` |
| `narrative` | ~647 | Everything else |
| `table` | ~17 | pdfplumber table detection |

### Sample output

```json
{
  "block_type": "recommendation",
  "page_num": 8,
  "section": "DIAGNOSIS AND CLASSIFICATION",
  "text": "Impaired fasting glucose (IFG): FPG 110 mg/dL to 125 mg/dL",
  "evidence_grade": "A"
}
```

---

## 2. ICMR STW 2024

**Parser class:** `ICMRWorkflowParser` (`workflow.py`)  
**PDF:** `corpus/tier1_clinical/ICMR_STW_2024/ICMR_STW_Diabetes_T2DM_2024.pdf`  
**Page size:** 842×1634 pt (single very tall flowchart page)

### How it works

1. Tables are extracted first — the two embedded treatment-decision tables are the highest-value content.
2. Non-table words are extracted and grouped into lines by y-coordinate (±4 pt tolerance).
3. All non-table lines are emitted as `narrative`. No recommendation classification — the document is a clinical decision flowchart, not a guideline with discrete recommendations.
4. All blocks carry `section = "Treatment Workflow"`.

### Block types produced

| Type | Count | Trigger |
|------|-------|---------|
| `table` | 2 | Embedded treatment decision tables |
| `narrative` | ~79 | All other text (flowchart labels, arrows, notes) |

### Sample output

```json
{
  "block_type": "table",
  "page_num": 1,
  "section": "Treatment Workflow",
  "text": "ASSESS | CO-MORBIDITIES: Hypertension Dyslipidaemia CAD CKD | INVESTIGATION: HbA1c Creatinine K+ Fasting lipid ...",
  "evidence_grade": ""
}
```

---

## 3. ADA 2026

**Parser class:** `ADAJournalParser` (`ada_journal.py`)  
**PDFs:** `corpus/tier1_clinical/ADA_2026/ADA_2026_S01.pdf` through `ADA_2026_S15.pdf` (15 files)  
**Page size:** 594×783 pt, two-column journal layout  
**Note:** All 15 sections are merged into a single `parsed/ADA_2026.json`

### How it works

1. On each page, tables are extracted and stored with bounding boxes.
2. Remaining words (excluding header/footer bands and table regions) are split into **left and right columns** at `page.width / 2`.
3. Each column is processed top-to-bottom, grouped into lines by y-coordinate (±3 pt).
4. Line classification:
   - ALL CAPS line or matches `^S?\d+\.\d+` (section number) → `heading`
   - Line matches `^RECOMMENDATIONS?$` → `heading` that also sets `in_rec_section = True`
   - Line matches ADA numbered pattern `5.11a Text...` → `recommendation`
   - Line ends with `(A)/(B)/(C)/(E)` → `recommendation`
   - Line is inside a `Recommendations` section AND ≥ 40 characters → `recommendation`
   - Everything else → accumulated into `narrative`
5. Header band (`top < 48 pt`) and footer band (`top > 740 pt`) are stripped.

### Block types produced

| Type | Count | Trigger |
|------|-------|---------|
| `heading` | ~718 | ALL CAPS or section number lines |
| `recommendation` | ~340 | Numbered rec pattern, grade suffix, or inside Recommendations section |
| `narrative` | ~1,352 | Everything else |
| `table` | ~88 | pdfplumber table detection |

### Sample output

```json
{
  "block_type": "recommendation",
  "page_num": 3,
  "section": "Recommendations",
  "text": "5.11 For patients with type 2 diabetes not meeting glycemic targets, intensification of lifestyle therapy or pharmacological therapy should be considered. A",
  "evidence_grade": "A"
}
```

---

## 4. ICMR-NIN Food Composition Tables

**Parser class:** `ICMRNINParser` (`food_table.py`)  
**PDF:** `corpus/tier1_clinical/ICMR_NIN/ICMR_NIN_Indian_Food_Composition_Tables.pdf`  
**Page size:** 585 pages of nutritional data tables with rotated column headers

### How it works

pdfplumber `extract_tables()` returns nothing on this PDF (no detectable line structure). Custom word-coordinate approach:

1. All words extracted with bounding boxes.
2. Words clustered into lines by y-coordinate (±4 pt).
3. Lines are walked in order. A line whose first word matches `[A-Z]\d{3}` (food code pattern) starts a new food item accumulator. All subsequent lines — until the next food code — are appended to the accumulator. This handles food names that wrap to a second line.
4. For each accumulated food item, words are assigned to columns by their x-centre using hard-coded IFCT column boundaries:

| Column | x-range (pt) | Field |
|--------|-------------|-------|
| food_code | 0–100 | `food_code` |
| food_name | 100–312 | `food_name` |
| moisture | 312–371 | `moisture_g` |
| protein | 371–430 | `protein_g` |
| ash | 430–482 | `ash_g` |
| fat | 482–537 | `fat_g` |
| fiber total | 537–597 | `fiber_total_g` |
| fiber insoluble | 597–650 | `fiber_insoluble_g` |
| fiber soluble | 650–707 | `fiber_soluble_g` |
| carbohydrate | 707–769 | `carbohydrate_g` |
| energy (kJ) | 769+ | `energy_kj` |

5. Rows with no nutrient values at all are skipped. The `±` notation in values is handled by splitting on `±` and taking the base value.

### Block types produced

| Type | Count | Trigger |
|------|-------|---------|
| `food_row` | ~7,229 | Every row where first word matches `[A-Z]\d{3}` |

### Sample output

```json
{
  "block_type": "food_row",
  "page_num": 41,
  "section": "Food Composition Data",
  "text": "Bajra (Pennisetum typhoideum) (code: A003); carbohydrate 61.78 g/100g; energy 1456.0 kJ/100g; protein 10.96 g/100g; fat 5.43 g/100g; fiber total 11.49 g/100g",
  "evidence_grade": "",
  "food_data": {
    "food_code": "A003",
    "food_name": "Bajra (Pennisetum typhoideum)",
    "carbohydrate_g": 61.78,
    "energy_kj": 1456.0,
    "protein_g": 10.96,
    "fat_g": 5.43,
    "fiber_total_g": 11.49
  }
}
```

---

## 5. Anoop Misra South Asian Nutrition

**Parser class:** `ADAJournalParser` with Anoop-Misra-specific path (`ada_journal.py`)  
**PDF:** `corpus/tier1_clinical/Anoop_Misra_South_Asian_Nutrition/Anoop_Misra_Consensus_Dietary_Guidelines_Asian_Indians_2011.pdf`  
**Page size:** 612×792 pt, two-column journal layout

### How it works

Same two-column logic as ADA 2026, but with a source-specific code path for recommendation joining:

- When `source == "Anoop_Misra_South_Asian_Nutrition"`, the parser uses `_words_to_blocks_anoop()` instead of the generic method.
- This method detects numbered recommendation starters (`1.`, `2.`, `3.` at line start) inside a `Recommendations` section.
- Continuation lines — any line that does NOT start with a new number and is inside the same recommendations section — are appended to the open recommendation buffer.
- The buffer is flushed as a single joined `recommendation` block when the next numbered item or heading is found.
- Without this, each wrapped line would be a separate fragment.

### Block types produced

| Type | Count | Trigger |
|------|-------|---------|
| `heading` | ~6 | ALL CAPS lines |
| `recommendation` | ~37 | Numbered items in Recommendations sections, multi-line joined |
| `narrative` | ~28 | Non-recommendation paragraphs |

### Sample output

```json
{
  "block_type": "recommendation",
  "page_num": 7,
  "section": "Recommendations",
  "text": "3. Dietary intake of sodium from all sources (pickles, chutneys, namkeens, papads, bakery items, potato chips) should be limited. Avoid processed foods that have high salt content.",
  "evidence_grade": ""
}
```

---

## 6. KDIGO 2022 Diabetes Management in CKD

**Parser class:** `KDIGOParser` (`recommendation.py`)  
**PDF:** `corpus/tier2_condition/KDIGO_2022_DM_CKD/KDIGO_2022_Diabetes_Management_in_CKD.pdf`  
**Page size:** 594×783 pt, mixed layout

### How it works

1. Tables extracted first; their bounding boxes are used to exclude table words from text processing.
2. Non-table words are regrouped into lines (y-tolerance ±3 pt).
3. Line classification:
   - ALL CAPS → `heading`
   - Matches `^Recommendation\s*\d+[\.\d]*\s*:` or `^Practice\s*Point\s*[\d\.]+\s*:` or `We recommend` / `We suggest` / `Do not` / `In patients` → start of a `recommendation`
   - Subsequent lines are appended to the open recommendation until: a grade marker `(1A)`, `(2B)`, `(Not Graded)` etc. appears, OR 8 lines accumulated (safety valve)
   - Reference pages (header contains "references") are skipped for recommendation extraction to avoid citation fragments
4. KDIGO evidence grades follow the format `(Grade 1A)`, `(Grade 2B)`, `(Not Graded)`, `(Practice Point)` — extracted and stored in `evidence_grade`.

### Block types produced

| Type | Count | Trigger |
|------|-------|---------|
| `heading` | ~18 | ALL CAPS lines |
| `recommendation` | ~108 | `Recommendation X.Y.Z:` / `Practice Point X.Y.Z:` / `We recommend` etc. |
| `narrative` | ~152 | All other text |
| `table` | ~21 | pdfplumber table detection |

### Sample output

```json
{
  "block_type": "recommendation",
  "page_num": 21,
  "section": "DIABETES MANAGEMENT IN CHRONIC KIDNEY DISEASE",
  "text": "Recommendation 1.2.1: We recommend that treatment with ACEi or ARB be initiated in patients with diabetes, hypertension, and albuminuria, and that the dose be titrated to the maximum tolerated dose.",
  "evidence_grade": "1B"
}
```

---

## 7. IDF-DAR Diabetes and Ramadan

**Parser class:** `IDFDARParser` (`recommendation.py`)  
**PDF:** `corpus/tier2_condition/IDF_DAR/IDF_DAR_Practical_Guidelines_Diabetes_Ramadan.pdf`  
**Page size:** 595×842 pt (A4), 333 pages

### How it works

1. Tables extracted first.
2. Text read line by line.
3. Lines matching `(Very High|High|Moderate|Low)\s+Risk` → `heading`, and the risk level is stored as `risk_context`.
4. ALL CAPS lines (no bullet) → `heading`.
5. Lines starting with bullet (`●`, `•`, `◆`, `▶`, `-`) or a number (`1.`, `2.`) → `recommendation`. When a `risk_context` is active and not already in the section name, it is appended to `section` as `[Very High Risk]` etc. so retrieval knows which risk tier the recommendation applies to.
6. Everything else → `narrative`.

### Block types produced

| Type | Count | Trigger |
|------|-------|---------|
| `heading` | ~189 | ALL CAPS or risk-level headers |
| `recommendation` | ~1,359 | Bullet or numbered lines |
| `narrative` | ~1,471 | Everything else |
| `table` | ~371 | pdfplumber table detection |

### Sample output

```json
{
  "block_type": "recommendation",
  "page_num": 87,
  "section": "FASTING MANAGEMENT [Very High Risk]",
  "text": "Patients at very high risk should be strongly advised not to fast",
  "evidence_grade": ""
}
```

---

## 8. ESC 2023 Cardiovascular Disease in Diabetes

**Parser class:** `ADAJournalParser` (`ada_journal.py`)  
**PDF:** `corpus/tier2_condition/ESC_2023_CV_DM/ESC_2023_CVD_Diabetes_Guidelines.pdf`  
**Page size:** 595×794 pt, two-column journal layout

### How it works

Identical to ADA 2026 (same two-column layout, same `ADAJournalParser`). ESC recommendation tables use Class I / IIa / IIb / III notation which appears inline in the text. Evidence grades in the ESC format (`(B)`, `(C)`) are extracted by the same end-of-line grade pattern.

### Block types produced

| Type | Count | Trigger |
|------|-------|---------|
| `heading` | ~264 | ALL CAPS or section number lines |
| `recommendation` | ~852 | Grade suffix, numbered rec pattern, or inside Recommendations section |
| `narrative` | ~546 | Everything else |
| `table` | ~83 | pdfplumber table detection |

### Sample output

```json
{
  "block_type": "recommendation",
  "page_num": 18,
  "section": "Recommendations",
  "text": "Screening for diabetes is recommended in all individuals with CVD, using fasting glucose and/or HbA1c.",
  "evidence_grade": "B"
}
```

---

## 9. WHO HEARTS Technical Package

**Parser class:** `NarrativeParser` (`narrative.py`)  
**PDF:** `corpus/tier2_condition/WHO_HEARTS/WHO_HEARTS_Technical_Package.pdf`  
**Page size:** 595×842 pt (A4), 12 pages, single-column

### How it works

1. Tables extracted first.
2. `extract_text()` used for remaining text; split into lines.
3. Blank lines → paragraph boundary (flush pending buffer).
4. ALL CAPS line → `heading`.
5. Bullet lines (`•`, `●`, `◆`, `–`, `-`, `▶`) → own `narrative` block (not `recommendation` — WHO HEARTS is a process guide, not a clinical recommendation document).
6. All other lines accumulated into `pending`; flushed as single `narrative` block on paragraph break or heading.

### Block types produced

| Type | Count | Trigger |
|------|-------|---------|
| `heading` | ~2 | ALL CAPS lines |
| `narrative` | ~81 | Paragraphs and bullet items |
| `table` | ~10 | pdfplumber table detection |

### Sample output

```json
{
  "block_type": "narrative",
  "page_num": 4,
  "section": "",
  "text": "High blood pressure kills more people than any other condition — approximately 10 million people each year, more than all infectious diseases combined.",
  "evidence_grade": ""
}
```

---

## 10. Telemedicine Practice Guidelines India 2020

**Parser class:** `NarrativeParser` (`narrative.py`)  
**PDF:** `corpus/compliance/Telemedicine_Practice_Guidelines_India_2020.pdf`  
**Page size:** 595×842 pt (A4), 48 pages, single-column  
**Namespace:** `compliance` — queried by the bot to enforce scope boundaries, never shown to the patient

### How it works

Identical to WHO HEARTS (same `NarrativeParser`). Single-column policy document with tables. Tables are extracted as `table` blocks; running text becomes `narrative`; ALL CAPS section titles become `heading`.

### Block types produced

| Type | Count | Trigger |
|------|-------|---------|
| `heading` | ~6 | ALL CAPS lines |
| `narrative` | ~76 | Paragraphs |
| `table` | ~61 | pdfplumber table detection (heavy use of tables in this doc) |

### Sample output

```json
{
  "block_type": "narrative",
  "page_num": 5,
  "section": "TELEMEDICINE",
  "text": "The delivery of health care services, where distance is a critical factor, by all health care professionals using information and communication technologies for the exchange of valid information for diagnosis, treatment and prevention of disease.",
  "evidence_grade": ""
}
```

---

## Parser-to-source mapping summary

| Source | Parser class | File |
|--------|-------------|------|
| RSSDI_2022 | `RSSDirectParser` | `recommendation.py` |
| ICMR_STW_2024 | `ICMRWorkflowParser` | `workflow.py` |
| ADA_2026 | `ADAJournalParser` | `ada_journal.py` |
| ICMR_NIN | `ICMRNINParser` | `food_table.py` |
| Anoop_Misra_South_Asian_Nutrition | `ADAJournalParser` (Anoop path) | `ada_journal.py` |
| KDIGO_2022_DM_CKD | `KDIGOParser` | `recommendation.py` |
| IDF_DAR | `IDFDARParser` | `recommendation.py` |
| ESC_2023_CV_DM | `ADAJournalParser` | `ada_journal.py` |
| WHO_HEARTS | `NarrativeParser` | `narrative.py` |
| Telemedicine_Guidelines_2020 | `NarrativeParser` | `narrative.py` |
