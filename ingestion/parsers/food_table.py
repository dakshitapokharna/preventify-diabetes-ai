"""
ICMR-NIN Indian Food Composition Tables (IFCT 2017 / Dec 2024 rev.) parser.

The PDF is 585 pages of nutritional data tables.  Each page has one large
composition table with rotated column headers (they appear as scrambled/
reversed text in extract_text()).  Data rows are normally oriented.

Key structural facts (confirmed by coordinate inspection):
  - pdfplumber extract_tables() returns nothing — no detectable line structure
  - Food codes ([A-Z]\\d{3}) are in the leftmost column (x ≈ 49–100)
  - Food names occupy a wide text column (x ≈ 100–312); names sometimes
    wrap to a second line, pushing nutrient values to a different y-cluster
  - Nutrient columns are at fixed x-positions:
      moisture   ~312, protein ~371, ash ~430, fat ~482,
      fiber_total ~537, fiber_insoluble ~597, fiber_soluble ~650,
      carbohydrate ~707, energy_kj ~769

Strategy
────────
1. Extract all words per page.
2. Cluster words into lines by y-coordinate (±4 pt).
3. Walk lines in order: when a line starts with a food code, begin a new
   food-item accumulator; all subsequent lines (until the next food code)
   are appended to that accumulator.  This handles wrapped food names.
4. Assign accumulated words to columns by x-position using the hard-coded
   IFCT column boundaries.
5. Emit one ParsedBlock(block_type="food_row") per food item.

All values are per 100 g edible portion.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pdfplumber

from .base import BaseParser, ParsedBlock, ParsedDocument

# ── patterns ──────────────────────────────────────────────────────────────────

_FOOD_CODE_RE = re.compile(r"^[A-Z]\d{3}$")

# ── IFCT column X-boundaries (left edge of each column, pt) ───────────────────
# Derived from coordinate inspection of ICMR-NIN PDF page 41.
# Column assignment: word belongs to the column whose boundary is nearest
# (and to the left of) the word's centre.

_COL_NAMES = [
    "food_code",        # x < 100
    "food_name",        # 100 ≤ x < 312
    "moisture_g",       # 312 ≤ x < 371
    "protein_g",        # 371 ≤ x < 430
    "ash_g",            # 430 ≤ x < 482
    "fat_g",            # 482 ≤ x < 537
    "fiber_total_g",    # 537 ≤ x < 597
    "fiber_insoluble_g",# 597 ≤ x < 650
    "fiber_soluble_g",  # 650 ≤ x < 707
    "carbohydrate_g",   # 707 ≤ x < 769
    "energy_kj",        # x ≥ 769
]

# Left edges; last entry is a sentinel for the rightmost column
_COL_X_STARTS = [0, 100, 312, 371, 430, 482, 537, 597, 650, 707, 769, 9999]

# Fields to keep in food_data
_KEY_NUTRIENTS = {
    "food_code", "food_name",
    "carbohydrate_g", "energy_kj", "protein_g", "fat_g", "fiber_total_g",
}

_Y_TOLERANCE = 4   # pt — words within this vertical distance → same line


def _parse_number(s: str) -> Optional[float]:
    """Parse a numeric string; handles ± notation by taking the base value."""
    s = s.split("±")[0].split("±")[0].strip()
    try:
        return float(s.replace(",", "").replace("–", "-").replace("−", "-"))
    except ValueError:
        return None


def _col_index(word: dict) -> int:
    """Return the column index for a word based on its x-centre."""
    centre = (word["x0"] + word["x1"]) / 2
    for i in range(len(_COL_X_STARTS) - 1):
        if _COL_X_STARTS[i] <= centre < _COL_X_STARTS[i + 1]:
            return i
    return len(_COL_NAMES) - 1


def _cluster_lines(words: list[dict]) -> list[list[dict]]:
    """Group words into lines by top-coordinate proximity."""
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: w["top"])
    lines: list[list[dict]] = []
    current: list[dict] = [sorted_words[0]]
    for w in sorted_words[1:]:
        if w["top"] - current[0]["top"] <= _Y_TOLERANCE:
            current.append(w)
        else:
            lines.append(sorted(current, key=lambda x: x["x0"]))
            current = [w]
    lines.append(sorted(current, key=lambda x: x["x0"]))
    return lines


def _correct_column_shift(food_data: dict) -> None:
    """
    Fix column mis-assignment for fish, seafood, and meat sections.

    The parser was calibrated on page 41 (cereals). Fish/seafood/meat pages later
    in the IFCT PDF have different column x-positions, causing three systematic
    mis-assignments that produce biologically impossible values:

      1. carbohydrate_g > 100  →  this is actually energy_kj
         (fish/meat carbohydrate is always <1 g/100 g; kJ values are 200–800)

      2. protein_g > 50        →  this is actually moisture_%
         (no whole food has >50 g protein/100 g; fish moisture is 65–85%)

      3. fiber_total_g appears with a plausible fat value (0.5–15) when
         carbohydrate was already flagged as mis-assigned →  this is actually fat_g
         (fish/meat have no measurable dietary fibre)

    Recovery logic:
      - Move mis-assigned carbohydrate to energy_kj (only if energy slot is empty)
      - Move mis-assigned protein to moisture_g; zero out protein slot
      - If energy slot was empty before correction, promote fiber→fat
    """
    carb = food_data.get("carbohydrate_g")
    protein = food_data.get("protein_g")
    energy = food_data.get("energy_kj")
    fiber = food_data.get("fiber_total_g")

    carb_shifted = carb is not None and carb > 100
    protein_shifted = protein is not None and protein > 50

    if carb_shifted:
        if energy is None:
            food_data["energy_kj"] = carb
        food_data["carbohydrate_g"] = None

    if protein_shifted:
        food_data["moisture_g"] = protein
        food_data["protein_g"] = None

    # When both carb and protein were shifted, the fat value lands in the
    # fiber_total_g slot (fiber columns shift left along with the others).
    if carb_shifted and protein_shifted and fiber is not None:
        if food_data.get("fat_g") is None:
            food_data["fat_g"] = fiber
        food_data["fiber_total_g"] = None


def _build_block(
    food_code: str,
    accumulated_words: list[dict],
    page_num: int,
) -> Optional[ParsedBlock]:
    """Convert accumulated words for one food item into a ParsedBlock."""
    col_tokens: dict[int, list[str]] = {}
    for w in accumulated_words:
        idx = _col_index(w)
        col_tokens.setdefault(idx, []).append(w["text"].strip())

    food_name_tokens = col_tokens.get(1, [])
    food_name = " ".join(t for t in food_name_tokens if t).strip()

    food_data: dict = {"food_code": food_code, "food_name": food_name}

    # Nutrient columns: indices 2..10 → _COL_NAMES[2..10]
    for idx in range(2, len(_COL_NAMES)):
        label = _COL_NAMES[idx]
        tokens = col_tokens.get(idx, [])
        raw = "".join(tokens)
        food_data[label] = _parse_number(raw)

    # Fix column mis-assignment for fish/seafood/meat pages
    _correct_column_shift(food_data)

    # Drop rows whose energy value is physically impossible — these are garbled
    # multi-food-code rows where the parser concatenated numbers from adjacent
    # PDF lines (e.g. "400" + "1083" → 4001083). Maximum real food energy is
    # ~3700 kJ/100g (pure fat). Anything above 4000 is a concatenation artifact.
    energy_val = food_data.get("energy_kj")
    if energy_val is not None and energy_val > 4000:
        return None

    # Similarly, carbohydrate_g > 100 is impossible after column correction
    # (should have been moved to energy by _correct_column_shift); drop if still present
    carb_val = food_data.get("carbohydrate_g")
    if carb_val is not None and carb_val > 100:
        return None

    # Skip rows that have no nutrient data at all
    has_any = any(
        food_data.get(n) is not None
        for n in ("carbohydrate_g", "protein_g", "fat_g", "energy_kj")
    )
    if not has_any and not food_name:
        return None

    filtered = {k: v for k, v in food_data.items() if k in _KEY_NUTRIENTS}

    carb = food_data.get("carbohydrate_g")
    energy = food_data.get("energy_kj")
    parts = [f"{food_name} (code: {food_code})"]
    if carb is not None:
        parts.append(f"carbohydrate {carb} g/100g")
    if energy is not None:
        parts.append(f"energy {energy} kJ/100g")
    for field in ("protein_g", "fat_g", "fiber_total_g"):
        val = food_data.get(field)
        if val is not None:
            label = field.replace("_g", "").replace("_", " ")
            parts.append(f"{label} {val} g/100g")

    return ParsedBlock(
        text="; ".join(parts),
        block_type="food_row",
        page_num=page_num,
        section="Food Composition Data",
        food_data=filtered,
    )


class ICMRNINParser(BaseParser):
    """Parser for ICMR-NIN Indian Food Composition Tables."""

    def parse(self, path: Path, source: str) -> ParsedDocument:
        doc = ParsedDocument(source=source, path=str(path))

        with pdfplumber.open(path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                words = page.extract_words(
                    x_tolerance=3, y_tolerance=2, keep_blank_chars=False
                )
                if not words:
                    continue

                lines = _cluster_lines(words)

                current_code: Optional[str] = None
                current_words: list[dict] = []

                for line in lines:
                    if not line:
                        continue
                    first = line[0]["text"].strip()

                    if _FOOD_CODE_RE.match(first):
                        # Flush previous food item
                        if current_code is not None:
                            block = _build_block(current_code, current_words, page_num)
                            if block:
                                doc.blocks.append(block)
                        current_code = first
                        # Accumulate remaining words in this line (exclude food code itself)
                        current_words = [w for w in line if w["text"].strip() != first or _col_index(w) != 0]
                    elif current_code is not None:
                        # Continuation line (wrapped name or nutrients on next line)
                        current_words.extend(line)

                # Flush last item on page
                if current_code is not None:
                    block = _build_block(current_code, current_words, page_num)
                    if block:
                        doc.blocks.append(block)

        return doc
