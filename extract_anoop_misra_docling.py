"""
Anoop Misra 2011 — Consensus Dietary Guidelines for Asian Indians
Docling-based extractor for RAG pipeline.

Why Docling over the existing pdfplumber parser:
  - The PDF is a two-column journal article with rotated table headers (Table 2
    fatty-acid data) and dense tabular data; pdfplumber mixes columns and
    produces garbled output for those pages.
  - Docling's layout model detects reading order, table boundaries, and column
    flow correctly.

RAG-specific design choices (beyond the ADA extractor):
  1. html.unescape() — decodes &lt; &gt; so clinical thresholds like
     "SFA < 10%", "TFA < 1%", "fiber >= 25 g/day" are literal characters.
  2. Grid table rendering — uses table.data.grid (cell-by-cell structure) so
     row spans don't duplicate content and footnotes collapse to one column.
  3. Section metadata comments — each top-level section gets an HTML comment
     with topic_tags and population scope so the chunker can attach them as
     chunk metadata.
  4. Indian food glossary — a curated glossary of Hindi/regional food names
     found in this paper is appended so the RAG system can answer queries using
     both English and Hindi terms.
  5. RAG document header — citation, year, population, retrieval_tier, and
     india_specific flag embedded once at the top.

Output: parsed/Anoop_Misra_docling.md

Usage:
    python extract_anoop_misra_docling.py
"""

from __future__ import annotations

import html
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PDF_PATH = Path(
    "corpus/tier1_clinical/Anoop_Misra_South_Asian_Nutrition/"
    "Anoop_Misra_Consensus_Dietary_Guidelines_Asian_Indians_2011.pdf"
)
OUT_DIR = Path("parsed")
OUT_FILE = OUT_DIR / "Anoop_Misra_docling.md"

SOURCE_KEY = "Anoop_Misra_South_Asian_Nutrition"
CITATION = "Misra A et al. Diabetes Technol Ther. 2011;13(Suppl 2):S83-S101"
YEAR = 2011

# ── RAG document-level metadata ───────────────────────────────────────────────
RAG_HEADER = f"""\
<!-- rag_metadata
  source: {SOURCE_KEY}
  title: Consensus Dietary Guidelines for Healthy Living and Prevention of Diabetes, Metabolic Syndrome, and Related Disorders in Asian Indians
  citation: {CITATION}
  year: {YEAR}
  population: Asian Indians (urban/semi-urban)
  topic_tags: nutrition, dietary_guidelines, macronutrients, cooking_oils, south_asian, T2DM_prevention, CVD_prevention
  retrieval_tier: core
  condition_trigger: null
  india_specific: true
  age_scope: adult
  evidence_grade: consensus
-->

# Consensus Dietary Guidelines for Asian Indians (2011)

**Source:** {SOURCE_KEY}
**Citation:** {CITATION}
**Population:** Asian Indians (urban/semi-urban)
**Scope:** T2DM, CVD, Metabolic Syndrome prevention; macronutrient recommendations,
cooking oil guidance, meal patterns, Indian food composition.

---
"""

# ── Section-level metadata map ─────────────────────────────────────────────────
# Map keywords that appear in section headings → topic_tags for that section's
# HTML comment. The chunker picks these up to tag each chunk.
SECTION_TAG_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"carbohydrate|fiber|fibre|glycemic|GI", re.I),
     "carbohydrates, fiber, glycemic_index"),
    (re.compile(r"fat|oil|fatty acid|SFA|MUFA|PUFA|TFA|trans", re.I),
     "fats, cooking_oils, fatty_acids"),
    (re.compile(r"protein", re.I),
     "protein, legumes, dairy"),
    (re.compile(r"salt|sodium|sugar|sweetener", re.I),
     "salt, sugar, micronutrients"),
    (re.compile(r"water|alcohol|beverage", re.I),
     "hydration, alcohol"),
    (re.compile(r"meal|eating|snack|fast food|cooking method", re.I),
     "meal_pattern, cooking_methods, food_choices"),
    (re.compile(r"table\s+\d|nutrient trend|physical activity", re.I),
     "data_table, epidemiology"),
    (re.compile(r"recommendation|guideline|summary|conclusion", re.I),
     "recommendations, summary"),
    (re.compile(r"introduction|background|objective|method", re.I),
     "background, methods"),
]

# ── Indian food & nutrition glossary ─────────────────────────────────────────
# Appended to the output so RAG can match Hindi/regional queries to content.
INDIAN_FOOD_GLOSSARY = """
---

## Indian Food & Nutrition Glossary

<!-- rag_metadata
  source: {source}
  section: glossary
  topic_tags: indian_foods, food_terminology, hindi_terms, regional_foods
  population: Asian Indians
-->

This glossary maps Hindi/regional food names used in the guidelines to their
English equivalents. Included so the RAG system can match both-language queries.

| Hindi / Regional Name | English Name | Nutritional Note |
| --- | --- | --- |
| Bajra | Pearl millet | High fiber, low GI; recommended whole grain |
| Ragi / Nachni | Finger millet | High calcium and iron; low GI |
| Jowar | Sorghum | Gluten-free whole grain; good fiber source |
| Jaun | Barley | Very low GI; beta-glucan rich (cholesterol lowering) |
| Kuttu | Buckwheat | Not a true cereal; high protein and rutin content |
| Makka / Makai | Maize / Corn | Moderate GI; whole form preferred over flour |
| Atta | Whole wheat flour | Preferred over maida; higher fiber and lower GI |
| Maida | Refined wheat flour | High GI; limit intake |
| Besan | Chickpea flour | High protein; low GI |
| Katori | Small bowl (~150 mL) | Standard Indian serving unit used in portion guidance |
| Daal / Dal | Lentils / Pulses | High protein, fiber; low GI; core protein source |
| Rajma | Kidney beans | Low GI; high protein and fiber |
| Chana | Chickpeas | Low GI; high protein; whole preferred over processed |
| Moong | Green gram / Mung bean | Low GI; easily digestible protein |
| Masoor | Red lentil | Moderate protein; low GI |
| Urad | Black gram | High protein; used in idli/dosa fermentation |
| Idli | Steamed fermented rice-lentil cake | Lower GI than white rice alone due to fermentation |
| Dosa | Fermented rice-lentil crepe | Moderate GI; fermentation improves nutrient bioavailability |
| Poha | Flattened rice | Moderate GI; commonly eaten as breakfast |
| Upma | Semolina porridge | Moderate GI; semolina (sooji/rava) is refined wheat |
| Sooji / Rava | Semolina | Refined wheat; moderate GI |
| Nendran banana | Kerala cooking banana | Higher starch content than dessert bananas; moderate GI when ripe |
| Kappa / Tapioca | Cassava | High GI when boiled; pair with protein/fat to lower GL |
| Vanaspati | Hydrogenated vegetable fat | High TFA; strictly avoid — listed in guidelines |
| Ghee | Clarified butter | Saturated fat; limit to < 1 tsp/day per guidelines |
| Mustard oil | Sarson ka tel | High ALA (omega-3); recommended in oil rotation blends |
| Rice bran oil | — | Balanced FA profile; suitable for high-heat cooking |
| Safflower oil | Kardi ka tel | High LA (omega-6); blend with mustard for FA balance |
| Groundnut oil | Mungfali tel | MUFA-rich; suitable for blending |
| Sesame oil | Til ka tel | MUFA + PUFA; moderate for cooking |
| Coconut oil | Nariyal tel | High SFA; limit per guideline SFA < 10% |
| Til | Sesame seeds | MUFA + PUFA + lignan source |
| Methi | Fenugreek | Soluble fiber; may improve postprandial glucose |
| Amla | Indian gooseberry | High vitamin C; antioxidant |
| Isabgol | Psyllium husk | Soluble fiber supplement; lowers cholesterol and glucose |
""".format(source=SOURCE_KEY)

# ── Matches a full markdown table block ───────────────────────────────────────
_MD_TABLE_RE = re.compile(
    r"(?m)^(\|[^\n]+\|\n)(\|[-: |]+\|\n)((?:\|[^\n]*\|\n)*)",
)

# ── Heading detector for section metadata injection ───────────────────────────
# Matches ATX headings: ## Section Title
_HEADING_RE = re.compile(r"(?m)^(#{1,4})\s+(.+)$")

# Generic headings that don't warrant per-section metadata (too noisy)
_SKIP_METADATA_SECTIONS: frozenset[str] = frozenset({
    "key principles", "key principle", "recommendations", "recommendation",
    "appendix 1", "appendix 2", "appendix 3", "appendix",
    "references", "acknowledgments", "acknowledgements",
    "conflict of interest", "disclosure", "funding", "authors",
    "supplementary", "figure", "table", "abstract", "introduction",
})

# ── ≥/≤ restoration for 2011 PDF font encoding issue ─────────────────────────
# This PDF encodes ≥ as U+0015 (NAK control character). Docling passes it
# through verbatim. Simple replacement of \x15 → ≥ covers all cases.
# ≤ does not appear in this PDF so no substitution needed for it.

def _restore_comparison_operators(text: str) -> str:
    """Replace U+0015 (PDF font artifact) with the correct ≥ symbol."""
    return text.replace("\x15", "≥")


def _clean_cell(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"\n+", " · ", text)   # multi-line cells → bullet separator
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("|", "/")
    return text


def _render_table_grid(table_item) -> str:
    """
    Render a Docling TableItem from its raw grid.
    - Spanning cells: emitted only at top-left position; spans become empty.
    - Footnote rows (all non-empty cells identical): collapsed to first column.
    Falls back to table_item.export_to_markdown() on any error.
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

    # Collapse footnote rows (all non-empty cells same text)
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
    """Return topic_tags string for a given section heading."""
    for pattern, tags in SECTION_TAG_MAP:
        if pattern.search(heading_text):
            return tags
    return "general"


def _inject_section_metadata(md: str) -> str:
    """
    Insert an HTML comment with rag_metadata after substantive ATX headings.
    Generic/structural headings (recommendations, appendix, references, etc.)
    are skipped to avoid comment spam in the output.
    """
    def _replacer(match: re.Match) -> str:
        hashes = match.group(1)
        title = match.group(2).strip()
        # Skip generic headings that appear many times and add no retrieval value
        if title.rstrip(".").lower() in _SKIP_METADATA_SECTIONS:
            return f"{hashes} {title}"
        tags = _section_tags(title)
        comment = (
            f"\n<!-- rag_metadata source={SOURCE_KEY} "
            f"section=\"{title}\" "
            f"topic_tags=\"{tags}\" "
            f"population=\"Asian Indians\" "
            f"year={YEAR} -->"
        )
        return f"{hashes} {title}{comment}"

    return _HEADING_RE.sub(_replacer, md)


def convert_document(pdf_path: Path) -> str:
    """Convert the Anoop Misra PDF to clean RAG-ready Markdown."""
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    doc = result.document

    # Full markdown — good for text, headings, lists, evidence thresholds
    # html.unescape decodes &lt; &gt; so "SFA < 10%", "TFA < 1%" are literal
    md = html.unescape(doc.export_to_markdown())
    # Restore ≥/≤ lost to 2011 PDF font encoding (encoded as non-ASCII whitespace)
    md = _restore_comparison_operators(md)

    # Replace each markdown table block with a grid-rendered version
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

    # Inject per-section metadata comments for chunker
    md = _inject_section_metadata(md)

    return md


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    if not PDF_PATH.exists():
        print(f"[ERROR] PDF not found: {PDF_PATH.resolve()}")
        sys.exit(1)

    print(f"Anoop Misra 2011 — Docling extractor")
    print(f"  Input  : {PDF_PATH.resolve()}")
    print(f"  Output : {OUT_FILE.resolve()}")
    print()
    print(f"  Converting ...", end=" ", flush=True)

    try:
        md = convert_document(PDF_PATH)
    except Exception as exc:
        print(f"ERROR — {exc}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    # Count rough quality signals
    grade_count = sum(
        md.count(tok) for tok in [" A\n", " B\n", "< 10%", "< 7%", "< 1%",
                                   "25-40 g", "50-60%", "≤ 30%", "≥ 25"]
    )
    # Count tables
    table_count = md.count("| --- |")
    print(f"OK  ({len(md):,} chars, ~{table_count} tables, ~{grade_count} threshold markers)")

    # Build final output
    full_md = RAG_HEADER + md + INDIAN_FOOD_GLOSSARY

    OUT_FILE.write_text(full_md, encoding="utf-8")
    print(f"\n  Saved : {OUT_FILE.resolve()}")
    print(f"  Total chars : {len(full_md):,}")


if __name__ == "__main__":
    main()
