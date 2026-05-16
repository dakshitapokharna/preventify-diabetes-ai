"""
KDIGO 2022 Clinical Practice Guideline for Diabetes Management in Chronic
Kidney Disease — pdfplumber extractor.

Why pdfplumber (not Docling):
  Docling's VLM layout model throws std::bad_alloc starting from page 13 of
  this PDF and skips pages 13–128 entirely — the entire clinical guideline
  body (all recommendations, the KDIGO Heatmap, drug-threshold tables, and
  the evidence grade matrix) vanishes. Only the cover and table-of-contents
  pages are extracted. pdfplumber extracts all 128 pages cleanly without
  memory errors.

No content post-processing:
  Raw text per page is written as-is (with <!-- page N --> markers).
  No table re-rendering, no cell cleaning, no content transformation.
  All annotation decisions are deferred — add _annotate_* passes in a future
  session once chunking strategy is confirmed.

Core pipeline:
  1. pdfplumber.open(pdf_path) — page-by-page text extraction
  2. <!-- page N --> markers inserted before each page
  3. RAG_HEADER prepended — document-level metadata block
  4. Written to parsed/

Trigger: fires only when the CKD flag is raised in the conversation engine.
Population: Adults with T2DM (or T1DM) and Chronic Kidney Disease at any
  stage (G1–G5, A1–A3), including dialysis and transplant.
india_specific: false — global KDIGO guideline; overrides all Tier 1 sources
  on any CKD-specific sub-query (eGFR thresholds, albuminuria management,
  CKD drug dose adjustment).

Output: parsed/KDIGO_2022_DM_CKD_docling.md

Usage:
    python ingestion/extractors/tier2/kdigo_2022.py
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent.parent.parent
PDF_PATH = ROOT / "corpus/tier2_condition/KDIGO_2022_DM_CKD/KDIGO_2022_Diabetes_Management_in_CKD.pdf"
OUT_DIR = ROOT / "parsed"
OUT_FILE = OUT_DIR / "KDIGO_2022_DM_CKD_docling.md"

SOURCE_KEY = "KDIGO_2022_DM_CKD"
CITATION = (
    "KDIGO Diabetes Work Group. KDIGO 2022 Clinical Practice Guideline for "
    "Diabetes Management in Chronic Kidney Disease. "
    "Kidney Int. 2022;102(5S):S1–S127"
)
YEAR = 2022

# ── RAG document-level header ─────────────────────────────────────────────────
RAG_HEADER = f"""\
<!-- rag_metadata
  source: {SOURCE_KEY}
  title: KDIGO 2022 Clinical Practice Guideline for Diabetes Management in Chronic Kidney Disease
  citation: {CITATION}
  year: {YEAR}
  population: Adults with T2DM or T1DM and Chronic Kidney Disease (CKD) at any stage (G1–G5, including dialysis and transplant)
  topic_tags: CKD, chronic_kidney_disease, eGFR, albuminuria, UACR, GFR_categories, heatmap, risk_stratification, Metformin, SGLT2_inhibitors, GLP1_agonists, MRA, finerenone, RAASi, ACE_inhibitor, ARB, HbA1c_targets, blood_pressure, potassium_monitoring, hyperkalemia, hypoglycemia, glucose_monitoring, CGM, anemia, cardiovascular_risk, dialysis, transplant, T2DM, T1DM, drug_dose_adjustment, kidney_function_thresholds
  retrieval_tier: triggered
  condition_trigger: ckd
  india_specific: false
  age_scope: adult
  evidence_grade: KDIGO_1A_1B_1C_1D_2A_2B_2C_2D
-->

# KDIGO 2022 Clinical Practice Guideline — Diabetes Management in CKD

**Source:** {SOURCE_KEY}
**Citation:** {CITATION}
**Population:** Adults with Type 2 or Type 1 diabetes who have Chronic Kidney
Disease at any stage (G1–G5, A1–A3), including patients on dialysis and kidney
transplant recipients.
**Scope:** CKD risk classification (KDIGO Heatmap — eGFR × Albuminuria grid),
HbA1c targets individualised by CKD stage, glycaemic monitoring (CGM in CKD),
cardioprotective and kidney-protective drug selection (SGLT2i, RAASi, MRA/
finerenone, GLP-1 RAs), Metformin and other drug dose adjustments by eGFR
threshold, blood pressure targets, potassium and anaemia management, lifestyle
modification, and referral to nephrology.

> **Retrieval note:** TRIGGERED source — queried only when the CKD flag fires
> (keywords: kidney / creatinine / eGFR / dialysis / albuminuria / UACR /
> nephrologist).  For standard T2DM management without CKD context, RSSDI 2022
> and ICMR STW 2024 take priority.  When the CKD flag is active, KDIGO 2022
> overrides all Tier 1 sources on CKD-specific sub-queries.
>
> KDIGO recommendation grade schema: strength 1 = Strong ("We recommend"),
> 2 = Weak / Conditional ("We suggest").  Evidence quality A = High, B = Moderate,
> C = Low, D = Very Low.  Combined as 1A, 1B, 1C, 1D, 2A, 2B, 2C, 2D.
>
> Key eGFR thresholds (verbatim from KDIGO 2022):
>   Metformin: continue if eGFR >= 30; use caution / reduce dose eGFR 30-45;
>     do not initiate if eGFR < 45; stop if eGFR < 30.
>   SGLT2 inhibitors: initiate if eGFR >= 20; continue even if eGFR falls below
>     initiation threshold (kidney-protective benefit preserved).
>   BP target: < 120 mmHg systolic if tolerated; RAASi (ACEi or ARB) preferred
>     for patients with albuminuria.
>   UACR >= 30 mg/g = albuminuria present; UACR >= 300 mg/g = severely increased.

---

"""


# ── Core extraction ───────────────────────────────────────────────────────────

def extract_document(pdf_path: Path) -> str:
    """Extract all pages via pdfplumber, raw text, no content transformation."""
    import pdfplumber

    pages_text: list[str] = []
    failed: list[int] = []

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        print(f"  Pages: {total}", flush=True)
        for i, page in enumerate(pdf.pages):
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                failed.append(i + 1)
                text = f"[PAGE {i+1} EXTRACTION FAILED: {exc}]"
            pages_text.append(f"<!-- page {i+1} -->\n{text}")

    if failed:
        print(f"  [WARN] Failed pages: {failed}")

    return "\n\n".join(pages_text)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    if not PDF_PATH.exists():
        print(f"[ERROR] PDF not found: {PDF_PATH.resolve()}")
        sys.exit(1)

    print("KDIGO 2022 Diabetes in CKD — pdfplumber extractor")
    print(f"  Input  : {PDF_PATH.resolve()}")
    print(f"  Output : {OUT_FILE.resolve()}")
    print()

    try:
        raw = extract_document(PDF_PATH)
    except Exception as exc:
        print(f"[ERROR] — {exc}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    full_md = RAG_HEADER + raw

    OUT_FILE.write_text(full_md, encoding="utf-8")

    # Quality signals
    egfr_refs   = len(re.findall(r"\beGFR\b", full_md, re.I))
    sglt2_refs  = len(re.findall(r"\bSGLT2\b", full_md, re.I))
    grade_refs  = len(re.findall(r"\b[12][ABCD]\b", full_md))
    ckd_g_refs  = len(re.findall(r"\bCKD\s+G[1-5]\b", full_md, re.I))
    page_markers = full_md.count("<!-- page ")

    print(f"  OK  ({len(full_md):,} chars, {page_markers} pages extracted)")
    print(f"  eGFR refs        : {egfr_refs}")
    print(f"  SGLT2 refs       : {sglt2_refs}")
    print(f"  Grade refs (1A/2B/etc): {grade_refs}")
    print(f"  CKD G-stage refs : {ckd_g_refs}")
    print(f"\n  Saved : {OUT_FILE.resolve()}")
    print(f"  Total chars : {len(full_md):,}")


if __name__ == "__main__":
    main()
