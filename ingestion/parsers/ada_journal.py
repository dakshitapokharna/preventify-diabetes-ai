"""
Two-column journal parser.

Handles the Diabetes Care / European Heart Journal column layout shared by:
  - ADA 2026 (S01–S15)  — 594 × 783 pt
  - ESC 2023             — 595 × 794 pt
  - Anoop Misra 2011     — 612 × 792 pt

Algorithm per page
──────────────────
1. Locate all tables (with bounding boxes) → extract as atomic blocks.
2. Extract words that do NOT overlap any table bbox.
3. Drop running headers/footers (fixed y-bands at top and bottom).
4. Split words into left and right columns at page.width / 2.
5. Group words into lines within each column; reconstruct text.
6. Classify each line: heading | recommendation | narrative.
7. Yield blocks in reading order: full left column, then full right column,
   tables interleaved by approximate vertical position.
"""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from .base import (
    BaseParser,
    ParsedBlock,
    ParsedDocument,
    extract_evidence_grade,
    group_words_into_lines,
    table_to_dicts,
    words_to_text,
)

# ── constants ─────────────────────────────────────────────────────────────────

HEADER_Y_MAX = 48      # strip lines whose top < this (running journal header)
FOOTER_Y_MIN = 740     # strip lines whose top > this (page footer / page number)
COLUMN_GAP = 8         # words within this many pts of midpoint → ambiguous; skip
MIN_LINE_LEN = 2       # ignore lines shorter than this many chars

# ADA recommendation section header (title-case or ALL CAPS)
_REC_SECTION_RE = re.compile(r"^RECOMMENDATIONS?\s*$", re.I)

# ALL-CAPS heading detection (≥4 consecutive capital words)
_ALLCAPS_RE = re.compile(r"^([A-Z][A-Z\s\-\/]{3,})$")

# Section number prefix e.g. "4.2" or "S4.2"
_SECNUM_RE = re.compile(r"^S?\d+\.\d+")

# ADA 2026 numbered recommendation: starts with "X.Y " or "X.Ya " (a = letter suffix)
# Evidence grade appears inline as standalone letter — not at end in parens
_ADA_NUMBERED_REC = re.compile(r"^\d+\.\d+[a-z]?\s+[A-Z]")

# Evidence grade at end of line — older format e.g. RSSDI (A)
_GRADE_RE = re.compile(r"\(([ABCE])\)\s*$")

# Inline standalone grade in ADA 2026: e.g. "cost savings A and" — extract last one
_INLINE_GRADE_RE = re.compile(r"\s([ABCE])\s")

# Anoop Misra 2011 numbered recommendation: starts with "N. " (integer + period + space)
# e.g. "1. Salt intake should be..." / "3. Recommended protein sources:"
# Sub-items use "a.", "b." — these are continuation lines, not new numbered recs
_ANOOP_NUMBERED_REC = re.compile(r"^\d+\.\s+\S")

# Source key for Anoop Misra — used to activate multi-line rec joining
_ANOOP_MISRA_SOURCE = "Anoop_Misra_South_Asian_Nutrition"


def _is_heading(text: str) -> bool:
    stripped = text.strip()
    # ADA numbered recommendations (1.1 Ensure...) must not be misread as headings.
    # They match _SECNUM_RE because S is optional, but they are always recommendations.
    if _ADA_NUMBERED_REC.match(stripped):
        return False
    if _ALLCAPS_RE.match(stripped):
        return True
    # Title-case section headers like "Recommendations", "Summary", "Introduction"
    if _REC_SECTION_RE.match(stripped):
        return True
    # Short section-number headings "S4.2 Glycemic Targets" (require S prefix)
    if _SECNUM_RE.match(stripped) and len(stripped) < 60:
        return True
    return False


def _bbox_overlaps(word: dict, bbox: tuple) -> bool:
    """True if word's box overlaps with table bbox (x0,top,x1,bottom)."""
    wx0, wtop, wx1, wbot = word["x0"], word["top"], word["x1"], word["bottom"]
    tx0, ttop, tx1, tbot = bbox
    return not (wx1 < tx0 or wx0 > tx1 or wbot < ttop or wtop > tbot)


class ADAJournalParser(BaseParser):
    """Parses two-column research-paper PDFs (ADA, ESC, Anoop Misra)."""

    def parse(self, path: Path, source: str) -> ParsedDocument:
        doc = ParsedDocument(source=source, path=str(path))
        current_section = ""
        in_rec_section = False

        with pdfplumber.open(path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                blocks = self._parse_page(
                    page, page_num, source, current_section, in_rec_section
                )
                for b in blocks:
                    if b.block_type == "heading":
                        current_section = b.text
                        # entering or leaving a RECOMMENDATIONS block
                        if _REC_SECTION_RE.match(b.text):
                            in_rec_section = True
                        elif _is_heading(b.text):
                            in_rec_section = False
                    doc.blocks.append(b)

        return doc

    # ── page-level ────────────────────────────────────────────────────────────

    def _parse_page(
        self,
        page,
        page_num: int,
        source: str,
        current_section: str,
        in_rec_section: bool,
    ) -> list[ParsedBlock]:
        mid = page.width / 2

        # 1. tables with bboxes
        table_objects = page.find_tables()
        table_bboxes = [t.bbox for t in table_objects]  # (x0,top,x1,bottom)
        raw_tables = [t.extract() for t in table_objects]

        # 2. words outside table regions and outside header/footer bands
        all_words = page.extract_words(x_tolerance=3, y_tolerance=3)
        content_words = [
            w for w in all_words
            if not self._in_header_footer(w, page.height)
            and not any(_bbox_overlaps(w, bb) for bb in table_bboxes)
        ]

        # 3. split into columns
        left_words = [w for w in content_words if w["x1"] < mid - COLUMN_GAP]
        right_words = [w for w in content_words if w["x0"] >= mid + COLUMN_GAP]

        blocks: list[ParsedBlock] = []

        # 4. text blocks: left then right
        for col_words in (left_words, right_words):
            col_blocks = self._words_to_blocks(
                col_words, page_num, source, current_section, in_rec_section
            )
            for b in col_blocks:
                if b.block_type == "heading":
                    current_section = b.text
                    if _REC_SECTION_RE.match(b.text):
                        in_rec_section = True
                    elif _is_heading(b.text):
                        in_rec_section = False
            blocks.extend(col_blocks)

        # 5. table blocks
        for raw in raw_tables:
            if not raw:
                continue
            dicts = table_to_dicts(raw)
            flat = " | ".join(
                " ".join(str(cell or "") for cell in row) for row in raw
            ).strip()
            if flat:
                blocks.append(
                    ParsedBlock(
                        text=flat,
                        block_type="table",
                        page_num=page_num,
                        section=current_section,
                        table_data=dicts,
                        raw_table=raw,
                    )
                )

        # 6. Merge split recommendations.
        # In two-column layout, a single recommendation often starts in the left
        # column and continues in the right column as a separate block.  Merge
        # consecutive recommendation blocks when the first ends mid-sentence
        # (no terminal punctuation) or the second starts with a lowercase letter.
        merged: list[ParsedBlock] = []
        for b in blocks:
            if (
                merged
                and merged[-1].block_type == "recommendation"
                and b.block_type == "recommendation"
                and (
                    not merged[-1].text.rstrip().endswith((".", ":", "?", "!"))
                    or (b.text and b.text[0].islower())
                )
            ):
                prev = merged[-1]
                joined_text = prev.text.rstrip() + " " + b.text.lstrip()
                # Re-extract grade from the joined text
                clean, grade = extract_evidence_grade(joined_text)
                if not grade:
                    inline = _INLINE_GRADE_RE.findall(joined_text)
                    grade = inline[-1] if inline else (prev.evidence_grade or b.evidence_grade)
                merged[-1] = ParsedBlock(
                    text=clean,
                    block_type="recommendation",
                    page_num=prev.page_num,
                    section=prev.section,
                    evidence_grade=grade,
                )
            else:
                merged.append(b)

        return merged

    # ── line-level ────────────────────────────────────────────────────────────

    def _words_to_blocks(
        self,
        words: list[dict],
        page_num: int,
        source: str,
        current_section: str,
        in_rec_section: bool,
    ) -> list[ParsedBlock]:
        lines = group_words_into_lines(words, y_tolerance=3)
        blocks: list[ParsedBlock] = []
        pending_lines: list[str] = []   # accumulate narrative lines

        def flush_narrative():
            if not pending_lines:
                return
            text = " ".join(pending_lines).strip()
            if len(text) >= MIN_LINE_LEN:
                blocks.append(
                    ParsedBlock(
                        text=text,
                        block_type="narrative",
                        page_num=page_num,
                        section=current_section,
                    )
                )
            pending_lines.clear()

        # ── Anoop Misra 2011: multi-line numbered recommendation joining ────────
        # The PDF uses "N. <text>" format (integer + period) for recommendations.
        # Each numbered item spans 1–4 wrapped lines; sub-items use "a.", "b." etc.
        # We accumulate continuation lines into the open numbered rec until the
        # next "N." starter, a heading, or the section ends.
        # This path is only activated for Anoop_Misra_South_Asian_Nutrition so
        # ADA 2026 and ESC 2023 are completely unaffected.
        if source == _ANOOP_MISRA_SOURCE:
            return self._words_to_blocks_anoop(
                lines, page_num, current_section, in_rec_section, blocks, pending_lines
            )
        # ── end Anoop Misra fast-path ──────────────────────────────────────────

        for line_words in lines:
            text = words_to_text(line_words)
            if len(text) < MIN_LINE_LEN:
                continue

            if _is_heading(text) or _REC_SECTION_RE.match(text):
                flush_narrative()
                blocks.append(
                    ParsedBlock(
                        text=text,
                        block_type="heading",
                        page_num=page_num,
                        section=current_section,
                    )
                )
                if _REC_SECTION_RE.match(text):
                    in_rec_section = True
                else:
                    in_rec_section = False
                current_section = text
                continue

            grade_match = _GRADE_RE.search(text)
            numbered_match = _ADA_NUMBERED_REC.match(text)

            # Classify as recommendation when:
            #   - explicit end-of-line grade (older format: "(A)")
            #   - ADA numbered recommendation pattern ("5.11 ...")
            #   - inside a RECOMMENDATIONS section AND substantively long
            is_recommendation = (
                grade_match
                or numbered_match
                or (in_rec_section and len(text) >= 40)
            )
            if is_recommendation:
                flush_narrative()
                clean, grade = extract_evidence_grade(text)
                # ADA 2026 inline grade: pick last standalone letter A/B/C/E
                if not grade and numbered_match:
                    inline = _INLINE_GRADE_RE.findall(text)
                    grade = inline[-1] if inline else ""
                blocks.append(
                    ParsedBlock(
                        text=clean,
                        block_type="recommendation",
                        page_num=page_num,
                        section=current_section,
                        evidence_grade=grade,
                    )
                )
            else:
                pending_lines.append(text)

        flush_narrative()
        return blocks

    def _words_to_blocks_anoop(
        self,
        lines: list[list[dict]],
        page_num: int,
        current_section: str,
        in_rec_section: bool,
        blocks: list[ParsedBlock],
        pending_lines: list[str],
    ) -> list[ParsedBlock]:
        """Anoop Misra-specific block builder.

        Joins multi-line numbered recommendations (1., 2., 3. …) into single
        blocks. Continuation lines — including sub-items (a., b.) and wrapped
        text — are appended to the open numbered rec. A new numbered rec or a
        heading closes the current one.
        """

        # Open numbered recommendation accumulator
        open_rec_lines: list[str] = []
        open_rec_page: int = page_num

        def flush_narrative():
            if not pending_lines:
                return
            text = " ".join(pending_lines).strip()
            if len(text) >= MIN_LINE_LEN:
                blocks.append(
                    ParsedBlock(
                        text=text,
                        block_type="narrative",
                        page_num=page_num,
                        section=current_section,
                    )
                )
            pending_lines.clear()

        def flush_open_rec():
            if not open_rec_lines:
                return
            text = " ".join(open_rec_lines).strip()
            if len(text) >= MIN_LINE_LEN:
                clean, grade = extract_evidence_grade(text)
                blocks.append(
                    ParsedBlock(
                        text=clean,
                        block_type="recommendation",
                        page_num=open_rec_page,
                        section=current_section,
                        evidence_grade=grade,
                    )
                )
            open_rec_lines.clear()

        for line_words in lines:
            text = words_to_text(line_words)
            if len(text) < MIN_LINE_LEN:
                continue

            # ── heading / section boundary ──────────────────────────────────
            if _is_heading(text) or _REC_SECTION_RE.match(text):
                flush_open_rec()
                flush_narrative()
                blocks.append(
                    ParsedBlock(
                        text=text,
                        block_type="heading",
                        page_num=page_num,
                        section=current_section,
                    )
                )
                if _REC_SECTION_RE.match(text):
                    in_rec_section = True
                else:
                    in_rec_section = False
                current_section = text
                continue

            # ── inside a numbered-rec section ────────────────────────────────
            if in_rec_section:
                if _ANOOP_NUMBERED_REC.match(text):
                    # New numbered item: close the previous one first
                    flush_open_rec()
                    flush_narrative()
                    open_rec_lines.append(text)
                    open_rec_page = page_num
                elif open_rec_lines:
                    # Continuation line (sub-items a./b./c. included) — append
                    open_rec_lines.append(text)
                else:
                    # Narrative line before the first numbered item in this section
                    pending_lines.append(text)
                continue

            # ── outside rec sections: classify normally ──────────────────────
            grade_match = _GRADE_RE.search(text)
            is_recommendation = grade_match
            if is_recommendation:
                flush_open_rec()
                flush_narrative()
                clean, grade = extract_evidence_grade(text)
                blocks.append(
                    ParsedBlock(
                        text=clean,
                        block_type="recommendation",
                        page_num=page_num,
                        section=current_section,
                        evidence_grade=grade,
                    )
                )
            else:
                pending_lines.append(text)

        flush_open_rec()
        flush_narrative()
        return blocks

    @staticmethod
    def _in_header_footer(word: dict, page_height: float) -> bool:
        return word["top"] < HEADER_Y_MAX or word["top"] > min(FOOTER_Y_MIN, page_height - 30)
