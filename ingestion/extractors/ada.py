"""
ADA 2026 Standards of Care — Docling-based extractor.

Uses a Vision-Language Model pipeline (Docling) instead of pdfplumber to
preserve the two-column layout, evidence grades (A/B/C/E), hierarchical
section numbers (1.1, 1.2), and complex tables (Table 1.1) that the generic
column splitter can lose.

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

ROOT = Path(__file__).parent.parent.parent
ADA_DIR = ROOT / "corpus/tier1_clinical/ADA_2026"
OUT_DIR = ROOT / "parsed"
OUT_FILE = OUT_DIR / "ADA_2026_docling.md"

CITATION = "Diabetes Care 2026;49(Suppl. 1)"
SOURCE_KEY = "ADA_2026"

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

    merged_parts: list[str] = [
        f"# ADA_2026 — Standards of Care in Diabetes 2026\n",
        f"**Citation:** {CITATION}  \n",
        f"**Sections:** {len(section_pdfs)}  \n",
        f"**Extractor:** Docling (VLM-based, grid table renderer)  \n\n---\n",
    ]

    for idx, pdf_path in enumerate(section_pdfs, start=1):
        section_id = pdf_path.stem
        print(f"[{idx:02d}/{len(section_pdfs)}] {section_id} ...", end=" ", flush=True)

        try:
            md = convert_section(pdf_path)
            header = build_section_header(pdf_path)
            merged_parts.append(header + md)
            grade_count = md.count(" A\n") + md.count(" B\n") + md.count(" C\n") + md.count(" E\n")
            print(f"OK  ({len(md):,} chars, ~{grade_count} grade markers)")
        except Exception as exc:
            print(f"ERROR — {exc}")
            merged_parts.append(
                build_section_header(pdf_path)
                + f"> **Extraction failed:** {exc}\n"
            )

    full_md = "\n".join(merged_parts)
    OUT_FILE.write_text(full_md, encoding="utf-8")

    print(f"\nDone.")
    print(f"  Total chars : {len(full_md):,}")
    print(f"  Saved       : {OUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
