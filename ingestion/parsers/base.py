from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ParsedBlock:
    """Single extracted content unit from a PDF page."""

    text: str
    block_type: str  # heading | recommendation | table | narrative | food_row
    page_num: int
    section: str = ""           # nearest ancestor heading
    evidence_grade: str = ""    # A / B / C / E (recommendations only)
    table_data: Optional[list[dict]] = None   # [{col: val, ...}, ...] for table blocks
    raw_table: Optional[list[list]] = None    # raw 2-D list from pdfplumber
    food_data: Optional[dict] = None          # {name, code, carb_g, energy_kj, ...}


@dataclass
class ParsedDocument:
    source: str
    path: str
    blocks: list[ParsedBlock] = field(default_factory=list)

    def recommendations(self) -> list[ParsedBlock]:
        return [b for b in self.blocks if b.block_type == "recommendation"]

    def tables(self) -> list[ParsedBlock]:
        return [b for b in self.blocks if b.block_type == "table"]

    def food_rows(self) -> list[ParsedBlock]:
        return [b for b in self.blocks if b.block_type == "food_row"]


class BaseParser:
    def parse(self, path: Path, source: str) -> ParsedDocument:
        raise NotImplementedError


# ── shared utilities ─────────────────────────────────────────────────────────

import re

EVIDENCE_GRADE_RE = re.compile(r"\(([ABCE])\)")


def extract_evidence_grade(text: str) -> tuple[str, str]:
    """Return (cleaned_text, grade) where grade is '' if not found.

    Searches anywhere in the line (not just end-of-line) so grades embedded
    before slide page numbers like '...first-line. (A) 41 RSSDI GUIDELINES'
    are still captured.  When multiple grades appear, the last one wins
    (RSSDI convention: trailing grade overrides any mid-sentence parenthetical).
    """
    matches = list(EVIDENCE_GRADE_RE.finditer(text))
    if not matches:
        return text, ""
    # Use the last match — most likely to be the recommendation grade rather
    # than a parenthetical letter mid-sentence (e.g. "option (a) or (b)").
    m = matches[-1]
    grade = m.group(1).upper()
    clean = (text[: m.start()] + text[m.end():]).strip()
    return clean, grade


def group_words_into_lines(
    words: list[dict], y_tolerance: int = 3
) -> list[list[dict]]:
    """Cluster pdfplumber word dicts by top-coordinate proximity."""
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (round(w["top"] / y_tolerance), w["x0"]))
    lines: list[list[dict]] = []
    current_line: list[dict] = [sorted_words[0]]
    for w in sorted_words[1:]:
        if abs(w["top"] - current_line[0]["top"]) <= y_tolerance:
            current_line.append(w)
        else:
            lines.append(sorted(current_line, key=lambda w: w["x0"]))
            current_line = [w]
    lines.append(sorted(current_line, key=lambda w: w["x0"]))
    return lines


def words_to_text(words: list[dict]) -> str:
    return " ".join(w["text"] for w in words).strip()


def table_to_dicts(raw: list[list]) -> list[dict]:
    """Convert pdfplumber raw table (list of rows) to list of dicts."""
    if not raw or not raw[0]:
        return []
    headers = [str(h or "").strip() for h in raw[0]]
    result = []
    for row in raw[1:]:
        d = {headers[i]: str(cell or "").strip() for i, cell in enumerate(row)}
        if any(v for v in d.values()):
            result.append(d)
    return result
