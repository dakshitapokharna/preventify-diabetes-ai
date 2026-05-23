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


# ── Annotation patterns ───────────────────────────────────────────────────────

# KDIGO recommendation grade: (1A), (2B), (1C), (2D) etc.
_KDIGO_GRADE_RE = re.compile(r"\(([12][ABCD])\)")

# KDIGO chapter headings in raw pdfplumber text
_CHAPTER_RE = re.compile(r"^(Chapter\s+\d+[:\.]?\s+.+)$", re.M | re.I)

# eGFR decision threshold lines — specific numeric thresholds used as drug stop/start points
_EGFR_THRESHOLD_RE = re.compile(
    r"eGFR\s*[<>≥≤]\s*\d+|eGFR\s+of\s+\d+|\beGFR\b.{0,40}\b(20|30|45|60)\b",
    re.I,
)

# Chapter-to-topic-tag map
_CHAPTER_TAG_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"comprehensive", re.I),
     "comprehensive_care, CKD_management, T2DM_CKD"),
    (re.compile(r"glycem", re.I),
     "glycemic_targets, HbA1c, CGM, glucose_monitoring"),
    (re.compile(r"lifestyle", re.I),
     "lifestyle_modification, nutrition, physical_activity, CKD_diet"),
    (re.compile(r"glucose.lower|pharmacol|drug|therap", re.I),
     "drug_selection, SGLT2_inhibitors, GLP1_agonists, metformin, drug_dose_adjustment"),
    (re.compile(r"self.manag|education|team.based|integrated", re.I),
     "self_management, patient_education, team_care"),
]


def _chapter_tags(chapter_text: str) -> str:
    for pattern, tags in _CHAPTER_TAG_MAP:
        if pattern.search(chapter_text):
            return tags
    return "CKD, T2DM_CKD, general"


def _inject_chapter_metadata(md: str) -> str:
    """Detect 'Chapter N: ...' lines in raw pdfplumber text and prepend rag_metadata."""
    def _replacer(match: re.Match) -> str:
        chapter_line = match.group(1).strip()
        tags = _chapter_tags(chapter_line)
        comment = (
            f"<!-- rag_metadata source={SOURCE_KEY} "
            f"section=\"{chapter_line}\" "
            f"topic_tags=\"{tags}\" "
            f"population=\"T2DM T1DM CKD\" "
            f"year={YEAR} -->\n"
        )
        return comment + chapter_line
    return _CHAPTER_RE.sub(_replacer, md)


def _annotate_recommendation_grades(md: str) -> str:
    """Prepend rag_metadata before any line carrying a KDIGO grade marker.

    KDIGO recommendations wrap across multiple lines; the closing grade like
    '(1B).' always appears on the last line of the recommendation, not on the
    opening 'Recommendation X.Y.Z:' line. So we annotate every line that
    contains a grade marker — regardless of whether it starts with 'Recommendation'.

    Grade strength: 1 = strong ('We recommend'), 2 = weak ('We suggest').
    Evidence quality: A=High, B=Moderate, C=Low, D=Very Low.
    """
    out_lines: list[str] = []
    for line in md.splitlines():
        if len(line.strip()) > 20:
            grades = _KDIGO_GRADE_RE.findall(line)
            if grades:
                primary = grades[0]
                strength = "strong" if primary.startswith("1") else "conditional"
                all_grades = ",".join(grades)
                comment = (
                    f"<!-- rag_metadata source={SOURCE_KEY} "
                    f"evidence_grade=\"{primary}\" "
                    f"all_grades=\"{all_grades}\" "
                    f"recommendation_strength=\"{strength}\" "
                    f"topic_tags=\"recommendation, grade_{primary}, CKD\" -->"
                )
                out_lines.append(comment)
        out_lines.append(line)
    return "\n".join(out_lines)


def _annotate_egfr_thresholds(md: str) -> str:
    """Prepend safety_critical rag_metadata before lines with eGFR drug decision thresholds.

    These are the hard stop/start lines for Metformin (<30/<45) and SGLT2i (≥20)
    — must be retrieved verbatim without chunk boundary splits.
    """
    out_lines: list[str] = []
    for line in md.splitlines():
        if len(line.strip()) > 40 and _EGFR_THRESHOLD_RE.search(line):
            comment = (
                f"<!-- rag_metadata source={SOURCE_KEY} "
                f"topic_tags=\"eGFR_threshold, drug_dose_adjustment, CKD, safety\" "
                f"safety_critical=true "
                f"chunk_note=\"zero_loss_standalone_node\" -->"
            )
            out_lines.append(comment)
        out_lines.append(line)
    return "\n".join(out_lines)


# ── Core extraction ───────────────────────────────────────────────────────────

def extract_document(pdf_path: Path) -> str:
    """Extract all pages via pdfplumber, with annotation passes applied."""
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

    raw = "\n\n".join(pages_text)
    raw = _inject_chapter_metadata(raw)
    raw = _annotate_recommendation_grades(raw)
    raw = _annotate_egfr_thresholds(raw)
    return raw


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
    egfr_refs        = len(re.findall(r"\beGFR\b", full_md, re.I))
    sglt2_refs       = len(re.findall(r"\bSGLT2\b", full_md, re.I))
    grade_refs       = len(re.findall(r"\b[12][ABCD]\b", full_md))
    ckd_g_refs       = len(re.findall(r"\bCKD\s+G[1-5]\b", full_md, re.I))
    page_markers     = full_md.count("<!-- page ")
    rec_annots       = full_md.count("evidence_grade=")
    egfr_annots      = full_md.count("safety_critical=true")
    chapter_annots   = full_md.count("rag_metadata source=") - 1  # minus doc header

    print(f"  OK  ({len(full_md):,} chars, {page_markers} pages extracted)")
    print(f"  eGFR refs              : {egfr_refs}")
    print(f"  SGLT2 refs             : {sglt2_refs}")
    print(f"  Grade refs (1A/2B/etc) : {grade_refs}")
    print(f"  CKD G-stage refs       : {ckd_g_refs}")
    print(f"  Chapter annotations    : {chapter_annots}")
    print(f"  Recommendation grade annotations : {rec_annots}")
    print(f"  eGFR safety_critical annotations : {egfr_annots}")
    print(f"\n  Saved : {OUT_FILE.resolve()}")
    print(f"  Total chars : {len(full_md):,}")


if __name__ == "__main__":
    main()
