"""
IDF-DAR Practical Guidelines — Diabetes and Ramadan — pdfplumber extractor.

Why pdfplumber (not Docling):
  Docling's VLM renderer crashes with `std::bad_alloc` on this 333-page PDF from
  page 14 onwards — the same issue as ICMR-NIN. pdfplumber extracts all 333 pages
  cleanly without memory errors.

Table rendering:
  pdfplumber's find_tables() locates real data tables by their ruled-line bounding
  boxes. For each page we:
    1. Identify real tables (≥ 2 rows, ≥ 2 columns, ≥ 30% non-empty cells).
    2. Extract body text from page regions OUTSIDE table bounding boxes so table
       content is not duplicated in the flat text stream.
    3. Render each real table as a markdown table.
    4. Combine body text and markdown tables in top-to-bottom reading order.
  Layout-artifact "tables" (pdfplumber mis-detecting multi-column text as a table)
  are filtered by the non-empty-cell threshold and fall through to plain text.
  No cell content is dropped or modified — None cells become empty columns.

RAG-specific annotations (insert comment lines only — no content modification):
  1. _annotate_risk_stratification_lines() — detects lines carrying IDF-DAR
     risk-factor or scoring terms and prepends a rag_metadata comment tagged
     risk_stratification so the chunker keeps scoring rows atomic.
  2. _annotate_meal_timing_adjustments() — detects lines co-occurring a meal-timing
     keyword (Suhoor/Sahur/Sehri/Iftar/predawn) with a drug-class name and prepends
     a rag_metadata comment with an explicit meal_context field.
  3. _annotate_break_fast_safety() — detects lines containing the exact IDF-DAR BG
     safety thresholds (< 70 mg/dL / < 3.9 mmol/L; > 300 mg/dL / > 16.7 mmol/L)
     and prepends a rag_metadata comment tagged safety_redline=true.

Trigger: fires only when the Ramadan flag is raised in the conversation engine.
Population: Adults with T2DM or T1DM intending to fast during Ramadan.
india_specific: false — IDF global guideline.

Output: parsed/IDF_DAR_Ramadan_docling.md

Usage:
    python ingestion/extractors/tier2/idf_dar.py
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent.parent.parent
PDF_PATH = ROOT / "corpus/tier2_condition/IDF_DAR/IDF_DAR_Practical_Guidelines_Diabetes_Ramadan.pdf"
OUT_DIR = ROOT / "parsed"
OUT_FILE = OUT_DIR / "IDF_DAR_Ramadan_docling.md"

SOURCE_KEY = "IDF_DAR_2021"
CITATION = (
    "IDF-DAR Practical Guidelines for Diabetes and Ramadan. "
    "International Diabetes Federation and DAR International Alliance, 2021"
)
YEAR = 2021

# ── RAG document-level header ─────────────────────────────────────────────────
RAG_HEADER = f"""\
<!-- rag_metadata
  source: {SOURCE_KEY}
  title: IDF-DAR Practical Guidelines for Diabetes and Ramadan
  citation: {CITATION}
  year: {YEAR}
  population: Adults with T2DM or T1DM intending to fast during Ramadan; covers pre-Ramadan assessment, fasting-period management, and post-Ramadan follow-up
  topic_tags: Ramadan, fasting, risk_stratification, suhoor, iftar, hypoglycemia_thresholds, DKA, glucose_monitoring, SMBG, insulin_adjustment, sulfonylurea_adjustment, SGLT2_inhibitors, GLP1_agonists, Ramadan_Nutrition_Plan, RNP, meal_timing, drug_adjustment, break_fast_criteria, dehydration, physical_activity, Eid
  retrieval_tier: triggered
  condition_trigger: ramadan
  india_specific: false
  age_scope: adult
  evidence_grade: IDF_DAR_consensus
-->

# IDF-DAR Practical Guidelines — Diabetes and Ramadan (2021)

**Source:** {SOURCE_KEY}
**Citation:** {CITATION}
**Population:** Adults with Type 2 or Type 1 diabetes who wish to fast during
Ramadan. Covers pre-Ramadan risk assessment, personalised fasting safety
categories (Very High / High / Moderate / Low risk), Suhoor and Iftar drug
adjustments by drug class, the Ramadan Nutrition Plan (RNP), SMBG thresholds
for breaking the fast, and post-Ramadan medication review.
**Scope:** Risk stratification (IDF-DAR point scoring), pharmacological management
per meal timing (Suhoor/Iftar), glucose monitoring frequency, nutrition, physical
activity during Tarawih/fasting, dehydration management, and Eid transition.

> **Retrieval note:** TRIGGERED source — queried only when the Ramadan flag fires.
> For standard T2DM management outside Ramadan, RSSDI 2022 and ICMR STW 2024
> take priority.
> IDF-DAR risk categories: Very High risk = do not fast (medical advice);
> High risk = can fast with close medical supervision; Moderate risk = can fast
> with standard monitoring; Low risk = can fast safely.
> SMBG safety thresholds (verbatim from IDF-DAR 2021): break fast if BG < 70 mg/dL
> (< 3.9 mmol/L) or BG > 300 mg/dL (> 16.7 mmol/L) at any time; check BG at
> Suhoor, midday, Iftar, and 2 hours post-Iftar; increase frequency if on insulin
> or sulfonylurea.

---

"""

# ── Annotation patterns ───────────────────────────────────────────────────────

_RISK_LINE_RE = re.compile(
    r"(?i)\b("
    r"risk.factor|risk.categor|risk.classif|risk.stratif|risk.score|"
    r"very.high.risk|high.risk|moderate.risk|low.risk|"
    r"do.not.fast|fasting.risk|"
    r"HbA1c.*point|point.*HbA1c|"
    r"hypoglycaemi.*point|point.*hypoglycaemi|"
    r"diabetes.type.*score|score.*diabetes.type|"
    r"duration.*point|point.*duration"
    r")\b"
)

_MEAL_TIMING_RE = re.compile(r"\b(suhoor|sahur|sehri|iftar|pre.?dawn|sunset.meal)\b", re.I)

_DRUG_CLASS_RE = re.compile(
    r"\b(insulin|sulfonylurea|sulphonylurea|metformin|gliptin|DPP.4|SGLT2|"
    r"gliflozin|GLP.1|liraglutide|semaglutide|glibenclamide|gliclazide|glimepiride|"
    r"empagliflozin|dapagliflozin|sitagliptin|vildagliptin)\b",
    re.I,
)

_BREAK_FAST_BG_RE = re.compile(
    r"(?:"
    r"(?:<|less\s+than|below)\s*(?:70\s*mg/dL|3\.9\s*mmol)"
    r"|(?:>|more\s+than|above|greater\s+than)\s*(?:300\s*mg/dL|16\.7\s*mmol)"
    r")",
    re.I,
)


# ── Table helpers ─────────────────────────────────────────────────────────────

def _is_real_table(rows: list[list]) -> bool:
    """
    Filter out layout-artifact tables that pdfplumber detects in multi-column
    text layouts.  A real table must have:
      - at least 2 rows
      - at least 2 columns
      - at least 30% of cells non-empty (not None and not blank string)
    """
    if len(rows) < 2:
        return False
    max_cols = max(len(r) for r in rows)
    if max_cols < 2:
        return False
    total = sum(len(r) for r in rows)
    non_empty = sum(
        1 for r in rows for c in r
        if c is not None and str(c).strip()
    )
    return (non_empty / total) >= 0.30


def _render_markdown_table(rows: list[list]) -> str:
    """
    Render a pdfplumber table (list of rows, each a list of cell strings/None)
    as a GitHub-flavored markdown table.
    - None cells become empty strings.
    - Newlines within cells become ' | ' to keep rows on one line.
    - Pipe characters within cell text are replaced with '/' to avoid breaking
      the markdown column delimiter.
    All original cell content is preserved.
    """
    def _fmt(cell) -> str:
        if cell is None:
            return ""
        s = str(cell).replace("|", "/").replace("\n", " ")
        return s.strip()

    if not rows:
        return ""

    # Normalise all rows to the same column count
    max_cols = max(len(r) for r in rows)
    norm = [[_fmt(c) for c in (r + [None] * (max_cols - len(r)))] for r in rows]

    header = "| " + " | ".join(norm[0]) + " |"
    sep    = "| " + " | ".join(["---"] * max_cols) + " |"
    body   = ["| " + " | ".join(row) + " |" for row in norm[1:]]

    return "\n".join([header, sep] + body)


# ── Per-page extraction ───────────────────────────────────────────────────────

def _extract_page(page) -> str:
    """
    Extract one page combining body text (outside table bounding boxes) and
    markdown-rendered data tables in top-to-bottom reading order.
    """
    # Locate tables and classify them
    try:
        found = page.find_tables()
    except Exception:
        found = []

    real: list = []
    for ft in found:
        try:
            rows = ft.extract()
        except Exception:
            continue
        if rows and _is_real_table(rows):
            real.append((ft.bbox, rows))

    # No real tables — return raw page text
    if not real:
        return page.extract_text() or ""

    # Sort tables top-to-bottom by their top edge
    real.sort(key=lambda x: x[0][1])

    segments: list[str] = []
    prev_bottom: float = 0.0

    for (x0, top, x1, bottom), rows in real:
        # Body text above this table
        if top > prev_bottom:
            try:
                above = page.crop((0, prev_bottom, page.width, top)).extract_text()
            except Exception:
                above = ""
            if above and above.strip():
                segments.append(above.strip())

        segments.append(_render_markdown_table(rows))
        prev_bottom = bottom

    # Body text below the last table
    if prev_bottom < page.height:
        try:
            below = page.crop((0, prev_bottom, page.width, page.height)).extract_text()
        except Exception:
            below = ""
        if below and below.strip():
            segments.append(below.strip())

    return "\n\n".join(segments)


# ── Annotation passes ─────────────────────────────────────────────────────────

def _annotate_risk_stratification_lines(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        if _RISK_LINE_RE.search(line) and len(line.strip()) > 30:
            out.append(
                f"<!-- rag_metadata source={SOURCE_KEY} "
                f"topic_tags=\"risk_stratification, IDF_DAR_scoring, fasting_risk\" "
                f"chunk_note=\"keep_atomic_large_window\" "
                f"population=\"T2DM Ramadan\" -->"
            )
        out.append(line)
    return "\n".join(out)


def _annotate_meal_timing_adjustments(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        has_meal = _MEAL_TIMING_RE.search(line)
        has_drug = _DRUG_CLASS_RE.search(line)
        if has_meal and has_drug and len(line.strip()) > 40:
            meal_ctx = has_meal.group(1).lower()
            if meal_ctx in ("sahur", "sehri"):
                meal_ctx = "suhoor"
            out.append(
                f"<!-- rag_metadata source={SOURCE_KEY} "
                f"meal_context=\"{meal_ctx}\" "
                f"topic_tags=\"meal_timing, drug_adjustment, {meal_ctx}, Ramadan\" "
                f"population=\"T2DM Ramadan\" -->"
            )
        out.append(line)
    return "\n".join(out)


def _annotate_break_fast_safety(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        if _BREAK_FAST_BG_RE.search(line) and len(line.strip()) > 40:
            out.append(
                f"<!-- rag_metadata source={SOURCE_KEY} "
                f"safety_redline=true "
                f"topic_tags=\"break_fast_criteria, glucose_threshold, patient_safety, "
                f"hypoglycemia, hyperglycemia\" "
                f"chunk_note=\"zero_loss_standalone_node\" "
                f"population=\"T2DM Ramadan\" -->"
            )
        out.append(line)
    return "\n".join(out)


# ── Core extraction ───────────────────────────────────────────────────────────

def extract_document(pdf_path: Path) -> str:
    import pdfplumber

    pages_text: list[str] = []
    failed: list[int] = []
    tables_rendered = 0

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        print(f"  Pages: {total}", flush=True)
        for i, page in enumerate(pdf.pages):
            try:
                # Count real tables on this page for reporting
                try:
                    found = page.find_tables()
                    for ft in found:
                        rows = ft.extract()
                        if rows and _is_real_table(rows):
                            tables_rendered += 1
                except Exception:
                    pass
                text = _extract_page(page)
            except Exception as exc:
                failed.append(i + 1)
                text = f"[PAGE {i+1} EXTRACTION FAILED: {exc}]"
            pages_text.append(f"<!-- page {i+1} -->\n{text}")

    if failed:
        print(f"  [WARN] Failed pages: {failed}")
    print(f"  Real tables rendered as markdown: {tables_rendered}", flush=True)

    return "\n\n".join(pages_text)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    if not PDF_PATH.exists():
        print(f"[ERROR] PDF not found: {PDF_PATH.resolve()}")
        sys.exit(1)

    print("IDF-DAR Practical Guidelines — pdfplumber extractor")
    print(f"  Input  : {PDF_PATH.resolve()}")
    print(f"  Output : {OUT_FILE.resolve()}")
    print()

    try:
        raw = extract_document(PDF_PATH)
    except Exception as exc:
        print(f"[ERROR] — {exc}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    annotated = _annotate_risk_stratification_lines(raw)
    annotated = _annotate_meal_timing_adjustments(annotated)
    annotated = _annotate_break_fast_safety(annotated)

    full_md = RAG_HEADER + annotated

    OUT_FILE.write_text(full_md, encoding="utf-8")

    suhoor_refs      = len(re.findall(r"\bsuhoor\b", full_md, re.I))
    iftar_refs       = len(re.findall(r"\biftar\b", full_md, re.I))
    risk_annotations = full_md.count("IDF_DAR_scoring")
    meal_annotations = full_md.count("meal_context=")
    safety_annotations = full_md.count("safety_redline=true")
    page_markers     = full_md.count("<!-- page ")
    md_tables        = full_md.count("\n| --- |")

    print(f"\n  OK  ({len(full_md):,} chars, {page_markers} pages, {md_tables} markdown tables)")
    print(f"  Suhoor refs               : {suhoor_refs}")
    print(f"  Iftar refs                : {iftar_refs}")
    print(f"  Risk annotations          : {risk_annotations}")
    print(f"  Meal-timing annotations   : {meal_annotations}")
    print(f"  Safety redline annotations: {safety_annotations}")
    print(f"\n  Saved : {OUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
