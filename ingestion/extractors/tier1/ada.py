"""
ADA 2026 Standards of Care — Docling-based extractor.

Uses a Vision-Language Model pipeline (Docling) instead of pdfplumber to
preserve the two-column layout, evidence grades (A/B/C/E), hierarchical
section numbers (1.1, 1.2), and complex tables (Table 1.1) that the generic
column splitter can lose.

Annotation passes applied after Docling extraction:
  1. _inject_section_metadata() — adds rag_metadata HTML comments after every
     substantive heading so the chunker can tag each chunk with topic context.
  2. _annotate_evidence_grades() — detects ADA trailing grade markers (single
     letter A/B/C/E at end of bullet recommendation lines) and prepends a
     rag_metadata comment so grade-filtered retrieval surfaces Grade A first.

Output: parsed/ADA_2026_docling.md  (single merged file across all 15 sections)

Usage:
    python extract_ada_docling.py              # all sections S01–S15
    python extract_ada_docling.py S03 S07      # specific sections only
"""

from __future__ import annotations

import html
import io
import re
import sys
from pathlib import Path

# Windows console safety
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent.parent.parent
ADA_DIR = ROOT / "corpus/tier1_clinical/ADA_2026"
OUT_DIR = ROOT / "parsed"
OUT_FILE = OUT_DIR / "ADA_2026_docling.md"

CITATION = "Diabetes Care 2026;49(Suppl. 1)"
SOURCE_KEY = "ADA_2026"
YEAR = 2026

# ── RAG document-level header ─────────────────────────────────────────────────
RAG_HEADER = f"""\
<!-- rag_metadata
  source: {SOURCE_KEY}
  title: ADA Standards of Care in Diabetes 2026
  citation: {CITATION}
  year: {YEAR}
  population: Adults and children with T1DM, T2DM, prediabetes, GDM; global with US focus
  topic_tags: T2DM, T1DM, glycemic_targets, drug_selection, elderly, CGM, hypoglycemia, CVD_risk, pregnancy, GDM, complication_screening, lifestyle, obesity, ADA_evidence_graded
  retrieval_tier: core
  condition_trigger: null
  india_specific: false
  age_scope: adult_and_paediatric
  evidence_grade: A/B/C/E
-->

"""

# ── Section-level topic tag map ───────────────────────────────────────────────
SECTION_TAG_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"glycem|HbA1c|A1c|target|control|fasting|postprandial|time.in.range|TIR", re.I),
     "glycemic_targets, HbA1c"),
    (re.compile(r"metformin|first.line|initial|monotherapy", re.I),
     "first_line_therapy, metformin"),
    (re.compile(r"sulfonyl|SU\b|glimep|glibenc|gliclaz", re.I),
     "sulfonylureas, drug_class"),
    (re.compile(r"DPP.4|gliptin|sitagliptin|vildagliptin|teneligliptin|alogliptin", re.I),
     "DPP4_inhibitors, drug_class"),
    (re.compile(r"SGLT2|gliflozin|dapagliflozin|empagliflozin|canagliflozin", re.I),
     "SGLT2_inhibitors, drug_class"),
    (re.compile(r"GLP.1|liraglutide|semaglutide|exenatide|dulaglutide|tirzepatide", re.I),
     "GLP1_agonists, drug_class"),
    (re.compile(r"insulin|basal|premix|bolus|NPH|degludec|glargine|aspart|lispro", re.I),
     "insulin, injectable_therapy"),
    (re.compile(r"thiazolidinedione|pioglitazone|TZD", re.I),
     "thiazolidinediones, drug_class"),
    (re.compile(r"obesity|weight|BMI|overweight|bariatric|GLP.1.*weight|tirzepatide", re.I),
     "obesity, weight_management"),
    (re.compile(r"lifestyle|diet|nutrition|physical.activity|exercise|MNT|meal.plan", re.I),
     "lifestyle_modification, nutrition, physical_activity"),
    (re.compile(r"screen|complication|nephropathy|retinopathy|neuropathy|foot|fundus|UACR", re.I),
     "complication_screening, microvascular"),
    (re.compile(r"CKD|kidney|renal|creatinine|eGFR|dialysis|albuminuria", re.I),
     "CKD, renal_dosing, eGFR"),
    (re.compile(r"cardio|cardiovascular|CVD|heart|coronary|ASCVD|atherosclerosis|statin", re.I),
     "cardiovascular, CVD_risk"),
    (re.compile(r"BP|blood.pressure|hypertension|antihypertensive", re.I),
     "hypertension, blood_pressure"),
    (re.compile(r"lipid|dyslipidemia|cholesterol|statin|triglyceride|LDL|HDL", re.I),
     "dyslipidemia, lipids, statins"),
    (re.compile(r"hypoglycemia|low.sugar|low.glucose|hypoglycaemia|severe.hypo", re.I),
     "hypoglycemia, safety"),
    (re.compile(r"elder|geriatric|older.adult|frail|age[d>]\s*6[05]|S12", re.I),
     "elderly, geriatric"),
    (re.compile(r"pregnan|gestational|GDM|antenatal|lactation|postpartum", re.I),
     "pregnancy, GDM"),
    (re.compile(r"fast|ramadan|roza|religious", re.I),
     "fasting, ramadan, religious"),
    (re.compile(r"diagnos|criteria|classify|classification|prediabetes|A1C.criti", re.I),
     "diagnosis, classification"),
    (re.compile(r"self.monitor|SMBG|CGM|continuous.glucose|flash.glucose|sensor", re.I),
     "glucose_monitoring, CGM, SMBG"),
    (re.compile(r"technolog|pump|closed.loop|AID|automated.insulin|device", re.I),
     "diabetes_technology, insulin_pump, CGM"),
    (re.compile(r"mental.health|depression|distress|anxiety|psychosoc|well.being", re.I),
     "mental_health, psychosocial"),
    (re.compile(r"social.determinant|SDOH|health.equity|disparit|access", re.I),
     "health_equity, SDOH"),
    (re.compile(r"child|paediatric|pediatric|adolescent|youth|school", re.I),
     "paediatric, children, adolescent"),
    (re.compile(r"hospital|inpatient|perioperative|critical.care|surgical", re.I),
     "inpatient, perioperative"),
    (re.compile(r"refer|escalat|specialist", re.I),
     "referral, escalation"),
    (re.compile(r"prevention|delay|prediabetes|risk.reduction|lifestyle.interv", re.I),
     "prevention, prediabetes"),
    (re.compile(r"smok|tobacco|alcohol|substance", re.I),
     "smoking, substance_use"),
]

_SKIP_METADATA_SECTIONS: frozenset[str] = frozenset({
    "recommendations", "recommendation", "references", "acknowledgments",
    "acknowledgements", "conflict of interest", "disclosure", "funding",
    "abbreviations", "appendix", "introduction", "background",
    "abstract", "foreword", "preface", "table of contents", "contents",
    "figure", "table", "glossary", "summary",
})

_HEADING_RE = re.compile(r"(?m)^(#{1,4})\s+(.+)$")

# ADA grade: bullet lines ending with a lone letter A/B/C/E (trailing whitespace OK)
_ADA_GRADE_RE = re.compile(r"\s+([ABCE])\s*$")

# Matches a full markdown table block as produced by Docling's export_to_markdown():
#   header row  |...|
#   separator   |---|
#   data rows   |...|  (one or more)
_MD_TABLE_RE = re.compile(
    r"(?m)^(\|[^\n]+\|\n)(\|[-: |]+\|\n)((?:\|[^\n]*\|\n)*)",
)


def _clean_cell(text: str) -> str:
    """Normalise whitespace, decode HTML entities, and escape markdown pipe chars."""
    text = html.unescape(text or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("|", "/")
    return text


def _render_table_grid(table_item) -> str:
    """
    Render a Docling TableItem from its raw grid instead of using
    export_to_markdown(), which collapses row-section headers into the
    cell below them and repeats footnote text across every column.

    Key behaviours:
    - Spanning cells: the same TableCell object appears at every grid
      position it covers.  We track by object identity so each cell's
      text is emitted only at its top-left position; all other positions
      in the span become empty cells.
    - Footnote / caption rows: when every non-empty cell in a row carries
      identical text (e.g. table footnotes that Docling assigns to all
      columns), we collapse them to the first column only.
    - Row-section separator rows: cells with row_section=True are usually
      full-width category headers styled differently in the PDF.  We render
      them as a full-width row with content in the first column and empty
      cells in the rest, so they stay visually distinct.
    """
    try:
        data = table_item.data
        grid = data.grid          # List[List[Optional[TableCell]]]
        num_cols = data.num_cols
    except Exception:
        return table_item.export_to_markdown()

    if not grid or num_cols == 0:
        return table_item.export_to_markdown()

    seen: set[int] = set()   # object ids of cells already emitted
    str_rows: list[list[str]] = []

    for grid_row in grid:
        row_cells: list[str] = []
        for cell in grid_row:
            if cell is None:
                row_cells.append("")
            elif id(cell) in seen:
                # This position is covered by a span we already rendered
                row_cells.append("")
            else:
                seen.add(id(cell))
                row_cells.append(_clean_cell(cell.text))

        # Pad to num_cols in case grid row is short
        while len(row_cells) < num_cols:
            row_cells.append("")
        str_rows.append(row_cells[:num_cols])

    if not str_rows:
        return table_item.export_to_markdown()

    # Collapse rows where every non-empty cell has the same text (footnotes)
    clean_rows: list[list[str]] = []
    for row in str_rows:
        non_empty = [c for c in row if c]
        if len(non_empty) > 1 and len(set(non_empty)) == 1:
            clean_rows.append([non_empty[0]] + [""] * (num_cols - 1))
        else:
            clean_rows.append(row)

    # Build markdown table
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
            f"population=\"T2DM T1DM global\" "
            f"year={YEAR} -->"
        )
        return f"{hashes} {title}{comment}"
    return _HEADING_RE.sub(_replacer, md)


def _annotate_evidence_grades(md: str) -> str:
    """Prepend rag_metadata comment before ADA bullet recommendations with grade markers.

    ADA format: bullet lines ending with a single letter grade, e.g.
        '- 6.1 Assess glycemic status at least twice yearly in patients ... A'
    Collects all grade letters on the line; primary = highest (A > B > C > E).
    """
    _GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "E": 3}

    out_lines: list[str] = []
    for line in md.splitlines():
        stripped = line.strip()
        if len(stripped) > 40 and stripped.startswith("-"):
            m = _ADA_GRADE_RE.search(line)
            if m:
                grade = m.group(1).upper()
                comment = (
                    f"<!-- rag_metadata source={SOURCE_KEY} "
                    f"evidence_grade=\"{grade}\" "
                    f"topic_tags=\"recommendation, grade_{grade}\" "
                    f"population=\"T2DM T1DM global\" -->"
                )
                out_lines.append(comment)
        out_lines.append(line)
    return "\n".join(out_lines)


def convert_section(pdf_path: Path) -> str:
    """Convert a single ADA section PDF to Markdown with clean table rendering."""
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    doc = result.document

    # Full markdown export handles text, headings, lists, and evidence grades well.
    # export_to_markdown() HTML-escapes < and > as &lt; &gt; — decode them back so
    # clinical values like "> 2.5 mg", "A1C < 7%", "T-score ≤ -2.5" are preserved.
    md = html.unescape(doc.export_to_markdown())

    # Replace each markdown table block with a grid-rendered version
    tables = list(doc.tables)   # in document order, same order as they appear in md
    counter: list[int] = [0]

    def _replace(match: re.Match) -> str:
        idx = counter[0]
        counter[0] += 1
        if idx < len(tables):
            return _render_table_grid(tables[idx]) + "\n\n"
        return match.group(0)

    md = _MD_TABLE_RE.sub(_replace, md)

    # Warn if table counts diverge (indicates a mismatch in the replacement)
    found = counter[0]
    if found != len(tables):
        print(f"\n  [WARN] {pdf_path.name}: {len(tables)} tables in doc but regex matched {found}", end="")

    md = _inject_section_metadata(md)
    md = _annotate_evidence_grades(md)

    return md


def build_section_header(pdf_path: Path) -> str:
    """Return a clear separator so sections are identifiable in the merged file."""
    section_id = pdf_path.stem  # e.g. ADA_2026_S03
    return (
        f"\n\n---\n\n"
        f"<!-- source: {SOURCE_KEY} | file: {pdf_path.name} | citation: {CITATION} -->\n\n"
        f"# {section_id}\n\n"
    )


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    requested = sys.argv[1:]
    if requested:
        section_pdfs = []
        for token in requested:
            token = token.upper()
            if not token.startswith("ADA_2026_"):
                token = f"ADA_2026_{token}"
            p = ADA_DIR / f"{token}.pdf"
            if not p.exists():
                print(f"[WARN] not found, skipping: {p}")
            else:
                section_pdfs.append(p)
    else:
        section_pdfs = sorted(ADA_DIR.glob("ADA_2026_S*.pdf"))

    if not section_pdfs:
        print(f"No ADA section PDFs found in {ADA_DIR.resolve()}")
        sys.exit(1)

    print(f"ADA 2026 Docling extractor")
    print(f"  Input  : {ADA_DIR.resolve()}")
    print(f"  Output : {OUT_FILE.resolve()}")
    print(f"  Sections: {len(section_pdfs)}")
    print()

    merged_parts: list[str] = [RAG_HEADER]

    for idx, pdf_path in enumerate(section_pdfs, start=1):
        section_id = pdf_path.stem
        print(f"[{idx:02d}/{len(section_pdfs)}] {section_id} ...", end=" ", flush=True)

        try:
            md = convert_section(pdf_path)
            header = build_section_header(pdf_path)
            merged_parts.append(header + md)
            grade_annots = md.count("evidence_grade=")
            section_annots = md.count("rag_metadata source=")
            print(f"OK  ({len(md):,} chars, {section_annots} section tags, {grade_annots} grade annotations)")
        except Exception as exc:
            print(f"ERROR — {exc}")
            merged_parts.append(
                build_section_header(pdf_path)
                + f"> **Extraction failed:** {exc}\n"
            )

    full_md = "\n".join(merged_parts)
    OUT_FILE.write_text(full_md, encoding="utf-8")

    total_grade_annots = full_md.count("evidence_grade=")
    total_section_annots = full_md.count("rag_metadata source=")
    print(f"\nDone.")
    print(f"  Total chars         : {len(full_md):,}")
    print(f"  Section annotations : {total_section_annots}")
    print(f"  Grade annotations   : {total_grade_annots}")
    print(f"  Saved               : {OUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
