"""
ICMR-NIN Indian Food Composition Tables (IFCT 2017, Dec 2024 rev.) — extractor.

Uses the existing ICMRNINParser (pdfplumber) which already handles IFCT's
unusual layout correctly (fixed-x column detection, food-code clustering).
Docling is not suitable for this 585-page table-dense PDF on CPU — it crashes
with std::bad_alloc or takes 10+ hours even with OCR disabled.

This script:
  1. Runs ICMRNINParser to get all food_row blocks.
  2. Groups blocks by IFCT food group (first letter of food code).
  3. Renders each group as a markdown table with a section heading.
  4. Adds a RAG document header with source metadata.

Output: parsed/ICMR_NIN_docling.md

Usage:
    python extract_icmr_nin_docling.py
"""

from __future__ import annotations

import io
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PDF_PATH = Path("corpus/tier1_clinical/ICMR_NIN/ICMR_NIN_Indian_Food_Composition_Tables.pdf")
OUT_DIR = Path("parsed")
OUT_FILE = OUT_DIR / "ICMR_NIN_docling.md"

SOURCE_KEY = "ICMR_NIN"
CITATION = (
    "Longvah T, Ananthan R, Bhaskarachary K, Venkaiah K. "
    "Indian Food Composition Tables. Hyderabad: NIN, ICMR; 2017 (Dec 2024 rev.)"
)
YEAR = 2017

# ── IFCT food group codes (first letter of food code) ─────────────────────────
_GROUP_NAMES: dict[str, str] = {
    "A": "Cereals and Millets",
    "B": "Grain Legumes",
    "C": "Vegetables",
    "D": "Fruits",
    "E": "Nuts and Oilseeds",
    "F": "Condiments and Spices",
    "G": "Sugars and Sugar Products",
    "H": "Mushrooms",
    "J": "Meat and Poultry",
    "K": "Marine Fish and Shellfish",
    "L": "Freshwater Fish",
    "M": "Other Seafood",
    "N": "Eggs",
    "O": "Milk and Milk Products",
    "P": "Edible Fats and Oils",
    "Q": "Beverages",
}

# ── RAG document-level header ─────────────────────────────────────────────────
RAG_HEADER = f"""\
<!-- rag_metadata
  source: {SOURCE_KEY}
  title: Indian Food Composition Tables (IFCT 2017, Dec 2024 rev.)
  citation: {CITATION}
  year: {YEAR}
  population: General Indian population
  topic_tags: food_composition, nutrition, carbohydrates, protein, fat, fiber, energy, Indian_foods
  retrieval_tier: core
  condition_trigger: null
  india_specific: true
  age_scope: all
  evidence_grade: GoI_reference_data
-->

# ICMR-NIN Indian Food Composition Tables (2017)

**Source:** {SOURCE_KEY}
**Citation:** {CITATION}
**Population:** General Indian population — authoritative reference for nutrient
values of Indian foods per 100 g edible portion.
**Scope:** ~2000+ Indian foods across 16 food groups. Primary authority for
carbohydrate, energy, protein, fat and fiber values used in dietary counselling.

> All values are per 100 g edible portion. Energy in kJ; macronutrients in g.

---
"""


def _group_label(food_code: str) -> str:
    letter = food_code[0].upper() if food_code else "?"
    return _GROUP_NAMES.get(letter, f"Group {letter}")


def _render_group_table(group_name: str, blocks: list) -> str:
    lines: list[str] = [
        f"\n## {group_name}\n",
        "<!-- rag_metadata"
        f" source={SOURCE_KEY}"
        f' section="{group_name}"'
        f' topic_tags="food_composition, {group_name.lower().replace(" ", "_")}"'
        f" year={YEAR} -->\n",
        "| Food Code | Food Name | Carb (g) | Protein (g) | Fat (g) | Fiber (g) | Energy (kJ) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for block in blocks:
        fd = block.food_data
        code = fd.get("food_code", "")
        name = (fd.get("food_name") or "").replace("|", "/")
        carb = fd.get("carbohydrate_g", "")
        prot = fd.get("protein_g", "")
        fat = fd.get("fat_g", "")
        fiber = fd.get("fiber_total_g", "")
        energy = fd.get("energy_kj", "")

        def _fmt(v) -> str:
            return "" if v is None else str(v)

        lines.append(
            f"| {code} | {name} | {_fmt(carb)} | {_fmt(prot)} | {_fmt(fat)} | {_fmt(fiber)} | {_fmt(energy)} |"
        )
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    if not PDF_PATH.exists():
        print(f"[ERROR] PDF not found: {PDF_PATH.resolve()}")
        sys.exit(1)

    # Import parser relative to project root
    sys.path.insert(0, str(Path(__file__).parent))
    from ingestion.parsers.food_table import ICMRNINParser

    print("ICMR-NIN Food Composition Tables — pdfplumber extractor")
    print(f"  Input  : {PDF_PATH.resolve()}")
    print(f"  Output : {OUT_FILE.resolve()}")
    print()
    print("  Parsing ...", end=" ", flush=True)

    parser = ICMRNINParser()
    doc = parser.parse(PDF_PATH, SOURCE_KEY)

    print(f"OK  ({len(doc.blocks):,} food rows extracted)")

    # Group blocks by food group (first letter of food code)
    groups: dict[str, list] = defaultdict(list)
    for block in doc.blocks:
        code = (block.food_data or {}).get("food_code", "?")
        letter = code[0].upper() if code and code[0].isalpha() else "?"
        groups[letter].append(block)

    print(f"  Food groups: {len(groups)} ({', '.join(sorted(groups))})")

    # Build markdown
    parts: list[str] = [RAG_HEADER]
    for letter in sorted(groups):
        group_name = _GROUP_NAMES.get(letter, f"Group {letter}")
        parts.append(_render_group_table(group_name, groups[letter]))

    full_md = "\n".join(parts)

    OUT_FILE.write_text(full_md, encoding="utf-8")

    table_count = full_md.count("| --- |")
    print(f"\n  Saved : {OUT_FILE.resolve()}")
    print(f"  Total chars : {len(full_md):,}")
    print(f"  Tables      : {table_count}")


if __name__ == "__main__":
    main()
