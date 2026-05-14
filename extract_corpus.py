"""
One-time corpus extraction runner.

Runs each registered parser on its PDF and writes parsed output to
parsed/<SOURCE>.md.  If the output looks correct, those files are
kept permanently — no re-run needed unless the source PDF changes.

Usage:
    python extract_corpus.py                  # all sources
    python extract_corpus.py RSSDI_2022       # single source
    python extract_corpus.py --list           # show available sources

Multi-file sources:
    ADA_2026 points to a directory.  All ADA_2026_S*.pdf files in that
    directory are parsed in order, their blocks concatenated, and the
    result written as a single parsed/ADA_2026.md.
"""

from __future__ import annotations

import io
import sys
import traceback
from pathlib import Path

# Make stdout safe on Windows consoles that can't handle all Unicode
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CORPUS_MAP: dict[str, str] = {
    "RSSDI_2022":                        "corpus/tier1_clinical/RSSDI_2022/RSSDI_Clinical_Practice_Recommendations_T2DM_2022.pdf",
    "ICMR_STW_2024":                     "corpus/tier1_clinical/ICMR_STW_2024/ICMR_STW_Diabetes_T2DM_2024.pdf",
    # ADA_2026 is a directory — all ADA_2026_S*.pdf files are merged into one output
    "ADA_2026":                          "corpus/tier1_clinical/ADA_2026",
    "ICMR_NIN":                          "corpus/tier1_clinical/ICMR_NIN/ICMR_NIN_Indian_Food_Composition_Tables.pdf",
    "Anoop_Misra_South_Asian_Nutrition": "corpus/tier1_clinical/Anoop_Misra_South_Asian_Nutrition/Anoop_Misra_Consensus_Dietary_Guidelines_Asian_Indians_2011.pdf",
    "KDIGO_2022_DM_CKD":                 "corpus/tier2_condition/KDIGO_2022_DM_CKD/KDIGO_2022_Diabetes_Management_in_CKD.pdf",
    "IDF_DAR":                           "corpus/tier2_condition/IDF_DAR/IDF_DAR_Practical_Guidelines_Diabetes_Ramadan.pdf",
    "ESC_2023_CV_DM":                    "corpus/tier2_condition/ESC_2023_CV_DM/ESC_2023_CVD_Diabetes_Guidelines.pdf",
    "WHO_HEARTS":                        "corpus/tier2_condition/WHO_HEARTS/WHO_HEARTS_Technical_Package.pdf",
    "Telemedicine_Guidelines_2020":      "corpus/compliance/Telemedicine_Practice_Guidelines_India_2020.pdf",
}

OUT_DIR = Path("parsed")
SAMPLE_N = 5
MAX_PREVIEW = 300


def extract(source: str, pdf_path: Path) -> dict:
    from ingestion.parsers.base import ParsedDocument
    from ingestion.parsers.registry import get_parser

    print(f"\n{'='*60}\n  {source}\n  {pdf_path}\n{'='*60}")

    if not pdf_path.exists():
        print(f"  [SKIP] path not found")
        return {"source": source, "error": "file not found"}

    try:
        parser = get_parser(source)

        # --- multi-file source: directory of PDFs ---
        if pdf_path.is_dir():
            section_pdfs = sorted(pdf_path.glob("ADA_2026_S*.pdf"))
            if not section_pdfs:
                raise FileNotFoundError(
                    f"No ADA_2026_S*.pdf files found in {pdf_path}"
                )
            print(f"  Directory source — merging {len(section_pdfs)} section PDFs:")
            for p in section_pdfs:
                print(f"    {p.name}")

            merged_doc = ParsedDocument(source=source, path=str(pdf_path))
            for section_pdf in section_pdfs:
                section_doc = parser.parse(section_pdf, source)
                merged_doc.blocks.extend(section_doc.blocks)
            doc = merged_doc

        # --- normal single-file source ---
        else:
            doc = parser.parse(pdf_path, source)

    except Exception as e:
        print(f"  [ERROR] {e}")
        traceback.print_exc()
        return {"source": source, "error": str(e)}

    counts: dict[str, int] = {}
    for b in doc.blocks:
        counts[b.block_type] = counts.get(b.block_type, 0) + 1

    print(f"\n  Blocks: {len(doc.blocks)}")
    for btype, n in sorted(counts.items()):
        print(f"    {btype:<20} {n}")

    # console sample per block type
    seen: dict[str, int] = {}
    for b in doc.blocks:
        if seen.get(b.block_type, 0) >= SAMPLE_N:
            continue
        print(f"\n  [{b.block_type}] p{b.page_num} | {b.section[:50]}")
        print(f"  {b.text[:MAX_PREVIEW]}")
        if b.evidence_grade:
            print(f"  grade={b.evidence_grade}")
        if b.food_data:
            print(f"  food_data={b.food_data}")
        seen[b.block_type] = seen.get(b.block_type, 0) + 1

    # write Markdown output — one block per section, easy to read and verify
    out_path = OUT_DIR / f"{source}.md"
    lines = [
        f"# {source}",
        f"",
        f"**Total blocks:** {len(doc.blocks)}  ",
        "  ".join(f"**{t}:** {n}" for t, n in sorted(counts.items())),
        "",
        "---",
        "",
    ]
    for b in doc.blocks:
        # Header line: type + page + section
        meta = f"**p{b.page_num}**"
        if b.section:
            meta += f" | {b.section}"
        if b.evidence_grade:
            meta += f" | grade: `{b.evidence_grade}`"
        lines.append(f"### [{b.block_type}] {meta}")
        lines.append("")
        if b.food_data:
            # food rows: show structured data as a mini table
            lines.append("| Field | Value |")
            lines.append("|-------|-------|")
            for k, v in b.food_data.items():
                lines.append(f"| {k} | {v} |")
        else:
            lines.append(b.text)
        lines.append("")
        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  Saved → {out_path}")
    return {"source": source, "total_blocks": len(doc.blocks), "counts": counts}


def main():
    args = sys.argv[1:]

    if "--list" in args:
        for s in sorted(CORPUS_MAP):
            print(s)
        return

    sources = [a for a in args if not a.startswith("-")] or list(CORPUS_MAP)
    unknown = [s for s in sources if s not in CORPUS_MAP]
    if unknown:
        print(f"Unknown: {unknown}\nAvailable: {sorted(CORPUS_MAP)}")
        sys.exit(1)

    OUT_DIR.mkdir(exist_ok=True)
    root = Path(__file__).parent
    summary = [extract(s, root / CORPUS_MAP[s]) for s in sources]

    print(f"\n\n{'='*60}\n  SUMMARY\n{'='*60}")
    for r in summary:
        if "error" in r:
            print(f"  {r['source']:<45} ERROR: {r['error']}")
        else:
            c = "  ".join(f"{t}={n}" for t, n in sorted(r["counts"].items()))
            print(f"  {r['source']:<45} total={r['total_blocks']}  [{c}]")

    print(f"\n  Output: {OUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
