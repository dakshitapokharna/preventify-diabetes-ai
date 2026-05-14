"""
ICMR Standard Treatment Workflow for T2DM 2024 — Docling-based extractor.

Why Docling over the existing ICMRWorkflowParser (pdfplumber):
  - The STW PDF contains multi-column algorithm boxes, treatment flowcharts
    rendered as tables, and rotated drug-class headers that pdfplumber
    splits across columns or loses entirely.
  - Docling's layout model recovers reading order and table boundaries
    correctly for clinical workflow documents.

RAG-specific design choices:
  1. html.unescape() — restores < > operators in clinical thresholds
     (e.g. "HbA1c < 7%", "eGFR < 30 mL/min/1.73 m²").
  2. Grid table rendering — cell identity tracking prevents row-span duplication
     in drug-class tables and algorithm step tables.
  3. Section metadata comments — substantive headings get rag_metadata HTML
     comments; the chunker uses them to tag each chunk.
  4. Unique: algorithm-step annotation — numbered step boxes (Step 1, Step 2)
     common in STW documents are tagged as treatment_algorithm so they rank
     high on clinical workflow queries.

Output: parsed/ICMR_STW_2024_docling.md

Usage:
    python extract_icmr_stw_docling.py
"""

from __future__ import annotations

import html
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PDF_PATH = Path("corpus/tier1_clinical/ICMR_STW_2024/ICMR_STW_Diabetes_T2DM_2024.pdf")
OUT_DIR = Path("parsed")
OUT_FILE = OUT_DIR / "ICMR_STW_2024_docling.md"

SOURCE_KEY = "ICMR_STW_2024"
CITATION = "ICMR Standard Treatment Workflow: Type 2 Diabetes Mellitus. New Delhi: ICMR; 2024"
YEAR = 2024

# ── RAG document-level metadata ───────────────────────────────────────────────
RAG_HEADER = f"""\
<!-- rag_metadata
  source: {SOURCE_KEY}
  title: ICMR Standard Treatment Workflow for Type 2 Diabetes Mellitus 2024
  citation: {CITATION}
  year: {YEAR}
  population: Adult T2DM patients, India (public health settings)
  topic_tags: treatment_workflow, clinical_algorithms, T2DM, drug_formulary, GoI_guidelines, HbA1c_targets, lifestyle, complication_screening
  retrieval_tier: core
  condition_trigger: null
  india_specific: true
  age_scope: adult
  evidence_grade: GoI_consensus
-->

# ICMR Standard Treatment Workflow for T2DM 2024

**Source:** {SOURCE_KEY}
**Citation:** {CITATION}
**Population:** Adult T2DM patients in India — reflects Government of India drug
formulary and what is actually prescribed in Indian public health (PHC/Aardram) settings.
**Scope:** Step-by-step treatment escalation, first-line therapy (Metformin positioning),
drug selection at each HbA1c threshold, complication screening intervals,
lifestyle modification protocols, and referral criteria.

> **Clinical priority note:** This is the most operationally grounded India source — it
> reflects GoI-approved drugs that PHC facilities stock. Use alongside RSSDI 2022 as a
> co-primary source. When ICMR STW and ADA 2026 conflict, prefer ICMR STW for drug
> availability / formulary decisions; prefer ADA for detailed clinical evidence grades.

---
"""

# ── Section-level metadata map ─────────────────────────────────────────────────
SECTION_TAG_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"step\s+\d|algorithm|escalat|intensif", re.I),
     "treatment_algorithm, drug_escalation"),
    (re.compile(r"metformin|first.line|initial|monotherapy", re.I),
     "first_line_therapy, metformin"),
    (re.compile(r"sulfonyl|SU\b|glimep|glibenc|gliclaz", re.I),
     "sulfonylureas, drug_class"),
    (re.compile(r"DPP.4|gliptin|sitagliptin|vildagliptin", re.I),
     "DPP4_inhibitors, drug_class"),
    (re.compile(r"SGLT2|gliflozin|dapagliflozin|empagliflozin", re.I),
     "SGLT2_inhibitors, drug_class"),
    (re.compile(r"GLP.1|liraglutide|semaglutide|exenatide", re.I),
     "GLP1_agonists, drug_class"),
    (re.compile(r"insulin|basal|premix|bolus|NPH", re.I),
     "insulin, injectable_therapy"),
    (re.compile(r"HbA1c|glycat|target|control", re.I),
     "glycemic_targets, HbA1c"),
    (re.compile(r"lifestyle|diet|nutrition|physical.activity|exercise|weight", re.I),
     "lifestyle_modification, nutrition, physical_activity"),
    (re.compile(r"screen|complication|nephropathy|retinopathy|neuropathy|foot", re.I),
     "complication_screening, microvascular"),
    (re.compile(r"CKD|kidney|renal|creatinine|eGFR", re.I),
     "CKD, renal_dosing, eGFR"),
    (re.compile(r"cardio|cardiovascular|CVD|heart|BP|blood.pressure|hypertension", re.I),
     "cardiovascular, CVD_risk, hypertension"),
    (re.compile(r"hypoglycemia|low.sugar|low.glucose|BG.< ", re.I),
     "hypoglycemia, safety"),
    (re.compile(r"refer|escalat|special|hospital", re.I),
     "referral, escalation"),
    (re.compile(r"pregnan|gestational|GDM|antenatal", re.I),
     "pregnancy, GDM"),
    (re.compile(r"elder|geriatric|older|frail|age[d>]", re.I),
     "elderly, geriatric"),
    (re.compile(r"diagnos|criteria|classify|classification|detection|screen", re.I),
     "diagnosis, classification"),
    (re.compile(r"self.monitor|SMBG|CGM|glucose.monitor", re.I),
     "glucose_monitoring, SMBG"),
]

# Headings too generic to warrant per-section metadata
_SKIP_METADATA_SECTIONS: frozenset[str] = frozenset({
    "recommendations", "recommendation", "references", "acknowledgments",
    "acknowledgements", "conflict of interest", "disclosure", "funding",
    "abbreviations", "appendix", "introduction", "background",
    "abstract", "foreword", "preface", "table of contents", "contents",
    "figure", "table", "glossary",
})

_MD_TABLE_RE = re.compile(
    r"(?m)^(\|[^\n]+\|\n)(\|[-: |]+\|\n)((?:\|[^\n]*\|\n)*)",
)

_HEADING_RE = re.compile(r"(?m)^(#{1,4})\s+(.+)$")

# Algorithm-step detector: "Step 1", "Step 2", etc. as standalone lines
_STEP_RE = re.compile(r"(?m)^(Step\s+\d+[:\.]?)", re.I)


def _clean_cell(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"\n+", " · ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("|", "/")
    return text


def _render_table_grid(table_item) -> str:
    """
    Render a Docling TableItem from its raw grid.
    - Spanning cells emitted only at top-left position.
    - Footnote rows (all non-empty cells identical) collapsed to first column.
    Falls back to export_to_markdown() on error.
    """
    try:
        data = table_item.data
        grid = data.grid
        num_cols = data.num_cols
    except Exception:
        return table_item.export_to_markdown()

    if not grid or num_cols == 0:
        return table_item.export_to_markdown()

    seen: set[int] = set()
    str_rows: list[list[str]] = []

    for grid_row in grid:
        row_cells: list[str] = []
        for cell in grid_row:
            if cell is None:
                row_cells.append("")
            elif id(cell) in seen:
                row_cells.append("")
            else:
                seen.add(id(cell))
                row_cells.append(_clean_cell(cell.text))
        while len(row_cells) < num_cols:
            row_cells.append("")
        str_rows.append(row_cells[:num_cols])

    if not str_rows:
        return table_item.export_to_markdown()

    clean_rows: list[list[str]] = []
    for row in str_rows:
        non_empty = [c for c in row if c]
        if len(non_empty) > 1 and len(set(non_empty)) == 1:
            clean_rows.append([non_empty[0]] + [""] * (num_cols - 1))
        else:
            clean_rows.append(row)

    sep = ["---"] * num_cols
    lines: list[str] = [
        "| " + " | ".join(clean_rows[0]) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for row in clean_rows[1:]:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


def _section_tags(heading_text: str) -> str:
    for pattern, tags in SECTION_TAG_MAP:
        if pattern.search(heading_text):
            return tags
    return "general"


def _inject_section_metadata(md: str) -> str:
    """Insert rag_metadata HTML comments after substantive ATX headings."""
    def _replacer(match: re.Match) -> str:
        hashes = match.group(1)
        title = match.group(2).strip()
        if title.rstrip(".").lower() in _SKIP_METADATA_SECTIONS:
            return f"{hashes} {title}"
        tags = _section_tags(title)
        comment = (
            f"\n<!-- rag_metadata source={SOURCE_KEY} "
            f"section=\"{title}\" "
            f"topic_tags=\"{tags}\" "
            f"population=\"T2DM India\" "
            f"year={YEAR} -->"
        )
        return f"{hashes} {title}{comment}"

    return _HEADING_RE.sub(_replacer, md)


def _annotate_algorithm_steps(md: str) -> str:
    """
    Wrap 'Step N' lines in a metadata comment so the chunker knows these are
    treatment algorithm steps (highest retrieval priority for clinical workflow queries).
    """
    def _replacer(match: re.Match) -> str:
        step_label = match.group(1)
        return (
            f"<!-- rag_metadata source={SOURCE_KEY} "
            f"topic_tags=\"treatment_algorithm, drug_escalation\" -->\n{step_label}"
        )
    return _STEP_RE.sub(_replacer, md)


def convert_document(pdf_path: Path) -> str:
    """Convert the ICMR STW 2024 PDF to clean RAG-ready Markdown."""
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    doc = result.document

    md = html.unescape(doc.export_to_markdown())

    tables = list(doc.tables)
    counter: list[int] = [0]

    def _replace(match: re.Match) -> str:
        idx = counter[0]
        counter[0] += 1
        if idx < len(tables):
            return _render_table_grid(tables[idx]) + "\n\n"
        return match.group(0)

    md = _MD_TABLE_RE.sub(_replace, md)

    found = counter[0]
    if found != len(tables):
        print(
            f"\n  [WARN] {len(tables)} tables in doc but regex matched {found}",
            end="",
        )

    md = _inject_section_metadata(md)
    md = _annotate_algorithm_steps(md)

    return md


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    if not PDF_PATH.exists():
        print(f"[ERROR] PDF not found: {PDF_PATH.resolve()}")
        sys.exit(1)

    print("ICMR STW 2024 — Docling extractor")
    print(f"  Input  : {PDF_PATH.resolve()}")
    print(f"  Output : {OUT_FILE.resolve()}")
    print()
    print("  Converting ...", end=" ", flush=True)

    try:
        md = convert_document(PDF_PATH)
    except Exception as exc:
        print(f"ERROR — {exc}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    # Quality signals
    hba1c_hits = md.count("HbA1c") + md.count("A1c") + md.count("A1C")
    step_hits = len(_STEP_RE.findall(md))
    table_count = md.count("| --- |")
    print(
        f"OK  ({len(md):,} chars, ~{table_count} tables, "
        f"~{hba1c_hits} HbA1c refs, ~{step_hits} algorithm steps)"
    )

    full_md = RAG_HEADER + md

    OUT_FILE.write_text(full_md, encoding="utf-8")
    print(f"\n  Saved : {OUT_FILE.resolve()}")
    print(f"  Total chars : {len(full_md):,}")


if __name__ == "__main__":
    main()
