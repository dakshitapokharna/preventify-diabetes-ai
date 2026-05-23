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
  4. Identifies Kerala-relevant foods within each group by name pattern matching
     and emits them as a separate Kerala sub-table with kerala_food=true metadata.
  5. Adds a RAG document header with source metadata.

Output: parsed/ICMR_NIN_docling.md

Usage:
    python ingestion/extractors/tier1/icmr_nin.py
"""

from __future__ import annotations

import io
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent.parent.parent
PDF_PATH = ROOT / "corpus/tier1_clinical/ICMR_NIN/ICMR_NIN_Indian_Food_Composition_Tables.pdf"
OUT_DIR = ROOT / "parsed"
OUT_FILE = OUT_DIR / "ICMR_NIN_docling.md"

SOURCE_KEY = "ICMR_NIN"
CITATION = (
    "Longvah T, Ananthan R, Bhaskarachary K, Venkaiah K. "
    "Indian Food Composition Tables. Hyderabad: NIN, ICMR; 2017 (Dec 2024 rev.)"
)
YEAR = 2017

# ── IFCT food group codes (first letter of food code) ─────────────────────────
# NB: The actual IFCT 2017 PDF uses a different letter scheme than the standard
# IFCT documentation. Verified from parsed data:
#   P = Marine Fish (not Edible Fats)
#   Q = Marine Crustaceans and Shellfish (not Beverages)
#   R = Marine Mollusks
#   S = Freshwater Fish and Shellfish
#   T = Edible Fats and Oils (not a standard IFCT letter)
#   K = Palm/Coconut Beverages (not Marine Fish)
#   I = Sugarcane and Sugarcane Juice
#   L = appears to contain Milk items (parser parsing artifact)
_GROUP_NAMES: dict[str, str] = {
    "A": "Cereals and Millets",
    "B": "Grain Legumes",
    "C": "Vegetables",
    "D": "Fruits",
    "E": "Nuts and Oilseeds",
    "F": "Condiments and Spices",
    "G": "Sugars and Sugar Products",
    "H": "Mushrooms",
    "I": "Sugarcane and Sugarcane Juice",
    "J": "Meat and Poultry",
    "K": "Palm and Coconut Beverages",
    "L": "Milk and Milk Products (L-series)",
    "M": "Other Seafood",
    "N": "Eggs",
    "O": "Milk and Milk Products",
    "P": "Marine Fish",
    "Q": "Marine Crustaceans and Shellfish",
    "R": "Marine Mollusks",
    "S": "Freshwater Fish and Shellfish",
    "T": "Edible Fats and Oils",
}

# ── Kerala food patterns per IFCT group letter ────────────────────────────────
# Each entry: (substring_to_match_in_food_name_lowercase, kerala_label)
# Food names in the parsed PDF are often garbled; patterns chosen to survive
# partial OCR noise. Add more entries as new Kerala foods are confirmed by the RD.
#
# IMPORTANT: letter codes reflect the ACTUAL food code letters in the parsed PDF,
# not the standard IFCT documentation. P = Marine Fish, Q = Crustaceans, etc.
# Many food names (coconut, jaggery) are too garbled to match; those are noted
# as "absent in parsed output — garbled" and deferred to chunking-time enrichment.
_KERALA_FOOD_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "A": [  # Cereals and Millets
        ("parboiled", "parboiled rice (pachari / puzhungal ari)"),
        ("raw, brown", "brown rice"),
        ("raw, milled", "milled white rice (vella ari)"),
        ("flakes", "rice flakes (aval / poha)"),
        ("puffed", "puffed rice (muri / kurmura)"),
    ],
    "C": [  # Vegetables
        ("colocasia", "colocasia / taro (chembu)"),
        ("taro", "taro (chembu)"),
        ("cassava", "tapioca / cassava (kappa)"),
        ("tapioca", "tapioca (kappa)"),
        ("jackfruit", "jackfruit (chakka)"),
        ("drumstick", "drumstick (muringa)"),
        ("plantain", "plantain / raw banana (ethakka)"),
        ("banana", "banana (pazham / ethakka)"),
        ("yam", "yam (chena)"),
        ("elephant foot", "elephant foot yam (chena)"),
    ],
    "D": [  # Fruits
        ("colocasia", "colocasia stem (chembu thandu)"),
        ("jackfruit", "jackfruit (chakka)"),
        ("banana", "banana (pazham)"),
        ("plantain", "plantain (ethakka)"),
        ("drumstick", "drumstick (muringa)"),
        # coconut absent — name garbled in parsed output
    ],
    "F": [  # Condiments and Spices
        ("curry", "curry leaves (kariveppila)"),
        ("tamarind", "tamarind (puli)"),
        ("kokum", "kokum / kudampuli"),
        ("garcinia", "kudampuli / gambooge (Garcinia cambogia)"),
        ("colocasia", "colocasia (chembu)"),
        # coconut absent — name garbled in parsed output
    ],
    "K": [  # Palm and Coconut Beverages (K002 = Cocos nucifera water = ilaneer)
        ("cocos", "tender coconut water (ilaneer / karikku vellam)"),
        ("nucifera", "tender coconut water (ilaneer / karikku vellam)"),
        ("borassus", "palmyra palm fruit (thaati nungu)"),
    ],
    "P": [  # Marine Fish — primary Kerala protein source
        ("sardine", "sardine (mathi)"),
        ("mackerel", "mackerel (ayala)"),
        ("anchovy", "anchovy (netholi / kozhuva)"),
        ("karimeen", "pearl spot (karimeen)"),
        ("pearl spot", "pearl spot (karimeen)"),
        ("pomfret", "pomfret (avoli)"),
        ("tuna", "tuna (choora)"),
        ("seer", "seer fish (neymeen)"),
        ("kingfish", "kingfish (neymeen)"),
        ("hilsa", "hilsa (mathi family)"),
        ("cat fish", "catfish (etta)"),
        ("prawn", "prawn (chemmeen)"),
        ("shrimp", "shrimp (chemmeen)"),
    ],
    "Q": [  # Marine Crustaceans and Shellfish
        ("crab", "crab (njandu)"),
        ("prawn", "prawn (chemmeen)"),
        ("lobster", "lobster"),
        ("oyster", "oyster"),
        ("clam", "clam (kakka)"),
    ],
    "R": [  # Marine Mollusks
        ("squid", "squid (koonthal)"),
        ("clam", "clam (kakka)"),
        ("mussel", "mussel (kallummekka)"),
        ("perna", "mussel (kallummekka)"),  # Perna viridis = green mussel
        ("octopus", "octopus"),
    ],
    "S": [  # Freshwater Fish and Shellfish
        ("prawn", "freshwater prawn (chemmeen)"),
        ("crab", "freshwater crab (njandu)"),
        ("catfish", "catfish (etta)"),
        ("cat fish", "catfish (etta)"),
    ],
    "T": [  # Edible Fats and Oils — food names are garbled ("oil 6", "seed oil 1")
        # coconut oil absent — name garbled; deferred to chunking-time enrichment
    ],
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

_TABLE_HEADER = (
    "| Food Code | Food Name | Carb (g) | Protein (g) | Fat (g) | Fiber (g) | Energy (kJ) |\n"
    "| --- | --- | --- | --- | --- | --- | --- |"
)


def _group_label(food_code: str) -> str:
    letter = food_code[0].upper() if food_code else "?"
    return _GROUP_NAMES.get(letter, f"Group {letter}")


def _fmt(v) -> str:
    return "" if v is None else str(v)


def _food_row(code: str, name: str, fd: dict) -> str:
    return (
        f"| {code} | {name} | {_fmt(fd.get('carbohydrate_g'))} | "
        f"{_fmt(fd.get('protein_g'))} | {_fmt(fd.get('fat_g'))} | "
        f"{_fmt(fd.get('fiber_total_g'))} | {_fmt(fd.get('energy_kj'))} |"
    )


def _match_kerala_label(group_letter: str, food_name: str) -> str | None:
    """Return the first matching Kerala label for a food name, or None."""
    patterns = _KERALA_FOOD_PATTERNS.get(group_letter.upper(), [])
    name_lower = food_name.lower()
    for pattern, label in patterns:
        if pattern in name_lower:
            return label
    return None


def _render_group_table(group_letter: str, group_name: str, blocks: list) -> str:
    rows: list[str] = []
    kerala_rows: list[tuple[str, str, dict, str]] = []  # (code, name, fd, kerala_label)

    for block in blocks:
        fd = block.food_data
        code = fd.get("food_code", "")
        name = (fd.get("food_name") or "").replace("|", "/")
        rows.append(_food_row(code, name, fd))
        label = _match_kerala_label(group_letter, name)
        if label:
            kerala_rows.append((code, name, fd, label))

    kerala_names = ", ".join(dict.fromkeys(lbl for _, _, _, lbl in kerala_rows))
    kerala_attr = (
        f' kerala_relevant=true kerala_foods="{kerala_names}"'
        if kerala_rows
        else ""
    )

    section_meta = (
        f"<!-- rag_metadata"
        f" source={SOURCE_KEY}"
        f' section="{group_name}"'
        f' topic_tags="food_composition, {group_name.lower().replace(" ", "_")}"'
        f" year={YEAR}{kerala_attr} -->"
    )

    parts: list[str] = [
        f"\n## {group_name}\n",
        section_meta + "\n",
        _TABLE_HEADER,
        *rows,
    ]

    if kerala_rows:
        kerala_topic = f"food_composition, {group_name.lower().replace(' ', '_')}, kerala_food"
        kerala_meta = (
            f"<!-- rag_metadata"
            f" source={SOURCE_KEY}"
            f' section="{group_name} — Kerala Relevant Foods"'
            f' topic_tags="{kerala_topic}"'
            f" kerala_food=true"
            f" year={YEAR} -->"
        )
        kerala_table_rows = [_food_row(code, name, fd) for code, name, fd, _ in kerala_rows]
        parts += [
            f"\n### {group_name} — Kerala Relevant Foods\n",
            kerala_meta + "\n",
            _TABLE_HEADER,
            *kerala_table_rows,
        ]

    return "\n".join(parts)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    if not PDF_PATH.exists():
        print(f"[ERROR] PDF not found: {PDF_PATH.resolve()}")
        sys.exit(1)

    sys.path.insert(0, str(ROOT))
    from ingestion.parsers.food_table import ICMRNINParser

    print("ICMR-NIN Food Composition Tables — pdfplumber extractor")
    print(f"  Input  : {PDF_PATH.resolve()}")
    print(f"  Output : {OUT_FILE.resolve()}")
    print()
    print("  Parsing ...", end=" ", flush=True)

    parser = ICMRNINParser()
    doc = parser.parse(PDF_PATH, SOURCE_KEY)

    print(f"OK  ({len(doc.blocks):,} food rows extracted)")

    # Deduplicate: same food code appears multiple times across IFCT sub-tables
    # (proximate composition on early pages, fatty acid/vitamin/mineral data on later
    # pages).  Keep only the highest-energy occurrence — that is always the proximate
    # composition row and is the one the RAG system needs for dietary counselling.
    best: dict[str, object] = {}
    for block in doc.blocks:
        code = (block.food_data or {}).get("food_code", "?")
        energy = (block.food_data or {}).get("energy_kj") or 0
        prev = best.get(code)
        if prev is None or energy > ((prev.food_data or {}).get("energy_kj") or 0):
            best[code] = block

    deduped = list(best.values())
    dropped = len(doc.blocks) - len(deduped)
    if dropped:
        print(f"  Deduplication    : {dropped} sub-table duplicates removed ({len(deduped):,} unique food codes)")

    groups: dict[str, list] = defaultdict(list)
    for block in deduped:
        code = (block.food_data or {}).get("food_code", "?")
        letter = code[0].upper() if code and code[0].isalpha() else "?"
        groups[letter].append(block)

    print(f"  Food groups: {len(groups)} ({', '.join(sorted(groups))})")

    parts: list[str] = [RAG_HEADER]
    kerala_group_count = 0
    kerala_row_count = 0

    for letter in sorted(groups):
        group_name = _GROUP_NAMES.get(letter, f"Group {letter}")
        section_md = _render_group_table(letter, group_name, groups[letter])
        parts.append(section_md)
        if "kerala_food=true" in section_md:
            kerala_group_count += 1
            kerala_row_count += section_md.count("\n| ") - section_md.count("| --- |") * 2

    full_md = "\n".join(parts)
    OUT_FILE.write_text(full_md, encoding="utf-8")

    table_count = full_md.count("| --- |")
    print(f"\n  Saved            : {OUT_FILE.resolve()}")
    print(f"  Total chars      : {len(full_md):,}")
    print(f"  Tables           : {table_count} (main: {len(groups)}, kerala sub-tables: {kerala_group_count})")
    print(f"  Kerala food rows : ~{kerala_row_count}")


if __name__ == "__main__":
    main()
