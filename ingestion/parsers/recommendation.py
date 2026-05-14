"""
Recommendation-document parser.

Handles three guideline families:

RSSDI 2022  — landscape 720×405 pt, single-column, bullet recommendations (●)
              headings in ALL CAPS, evidence grades (A)/(B)/(C)/(E) inline
KDIGO 2022  — 594×783 pt, mixed layout, "We recommend / We suggest" phrasing,
              grade notation  (Grade 1A) / (Grade 2B) etc.
IDF-DAR     — 595×842 pt, narrative + recommendation boxes, risk-stratification
              section headers (Very High Risk / High Risk / …)

Each document gets its own extraction method; they share a common output schema.
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
    table_to_dicts,
)

# ── shared patterns ───────────────────────────────────────────────────────────

_ALLCAPS_HEADING = re.compile(r"^[A-Z][A-Z\s\(\)\-\/&]{4,}$")
_BULLET = re.compile(r"^[●•◆▶\-–]\s*")
_NUMBERED = re.compile(r"^\d+[\.\)]\s+")
_GRADE_INLINE = re.compile(r"\(([ABCE])\)\s*$")                # (A) at end
_KDIGO_GRADE = re.compile(r"\((?:Grade\s*)?(\d[A-C]|Not Graded|Practice Point)\)", re.I)
# Matches numbered recommendation/practice-point labels that open a statement:
#   "Recommendation 1.2.1:"  "Recommendation1.3.1:"
#   "Practice Point 1.1.1:"  "PracticePoint2.1.1:"
#   Inline starters that may follow the label on the same line:
#   "We recommend"  "We suggest"  "Do not"  "In patients"
_KDIGO_REC_LABEL = re.compile(
    r"^(?:Recommendation\s*\d+[\.\d]*\s*:|"
    r"Practice\s*Point\s*[\d\.]+\s*:|"
    r"We recommend|We suggest|Do not\b|In patients\b)",
    re.I,
)
_IDF_RISK_HEADER = re.compile(
    r"(Very High|High|Moderate|Low)\s+Risk", re.I
)


def _clean_bullet(text: str) -> str:
    return _BULLET.sub("", text).strip()


# ── RSSDI ─────────────────────────────────────────────────────────────────────

class RSSDirectParser(BaseParser):
    """
    RSSDI Clinical Practice Recommendations 2022.
    Landscape 720×405, mostly single-column, bullet-point recommendations.
    """

    def parse(self, path: Path, source: str) -> ParsedDocument:
        doc = ParsedDocument(source=source, path=str(path))
        current_section = ""

        with pdfplumber.open(path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                # Extract tables first
                table_objects = page.find_tables()
                for t_obj in table_objects:
                    raw = t_obj.extract()
                    if raw:
                        flat = " | ".join(
                            " ".join(str(c or "") for c in row) for row in raw
                        )
                        doc.blocks.append(
                            ParsedBlock(
                                text=flat,
                                block_type="table",
                                page_num=page_num,
                                section=current_section,
                                table_data=table_to_dicts(raw),
                                raw_table=raw,
                            )
                        )

                text = page.extract_text() or ""
                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

                # Two open buffers: one for narrative, one for the current recommendation.
                # A recommendation stays open until the next bullet, heading, or page end —
                # so continuation lines (no bullet) are joined into the same block.
                narrative_buf: list[str] = []
                rec_buf: list[str] = []
                rec_grade: str = ""
                rec_page: int = page_num

                def flush_narrative():
                    if not narrative_buf:
                        return
                    joined = " ".join(narrative_buf).strip()
                    if len(joined) > 4:
                        doc.blocks.append(
                            ParsedBlock(
                                text=joined,
                                block_type="narrative",
                                page_num=page_num,
                                section=current_section,
                            )
                        )
                    narrative_buf.clear()

                def flush_rec():
                    nonlocal rec_grade, rec_page
                    if not rec_buf:
                        return
                    joined = " ".join(rec_buf).strip()
                    # Grade may arrive on a continuation line — re-extract from joined text
                    joined, grade = extract_evidence_grade(joined)
                    if not grade:
                        grade = rec_grade
                    if len(joined) > 4:
                        doc.blocks.append(
                            ParsedBlock(
                                text=joined,
                                block_type="recommendation",
                                page_num=rec_page,
                                section=current_section,
                                evidence_grade=grade,
                            )
                        )
                    rec_buf.clear()
                    rec_grade = ""

                for line in lines:
                    if len(line) <= 3:
                        continue

                    # Section heading: ALL CAPS, no leading bullet
                    if _ALLCAPS_HEADING.match(line) and not _BULLET.match(line):
                        flush_narrative()
                        flush_rec()
                        current_section = line
                        doc.blocks.append(
                            ParsedBlock(
                                text=line,
                                block_type="heading",
                                page_num=page_num,
                                section=current_section,
                            )
                        )
                        continue

                    # New bullet → close previous recommendation, open a new one
                    if _BULLET.match(line):
                        flush_narrative()
                        flush_rec()
                        clean = _clean_bullet(line)
                        clean, grade = extract_evidence_grade(clean)
                        rec_buf.append(clean)
                        rec_grade = grade
                        rec_page = page_num
                        continue

                    # No bullet, no heading → continuation of open recommendation
                    # (or plain narrative if no recommendation is open)
                    if rec_buf:
                        rec_buf.append(line)
                    else:
                        narrative_buf.append(line)

                flush_narrative()
                flush_rec()

        return doc


# ── KDIGO ─────────────────────────────────────────────────────────────────────

def _word_in_bbox(word: dict, bbox: tuple) -> bool:
    wx0, wtop, wx1, wbot = word["x0"], word["top"], word["x1"], word["bottom"]
    tx0, ttop, tx1, tbot = bbox
    return not (wx1 < tx0 or wx0 > tx1 or wbot < ttop or wtop > tbot)


class KDIGOParser(BaseParser):
    """
    KDIGO 2022 Guideline for Diabetes Management in CKD.
    594×783 pt, recommendations start with 'We recommend / We suggest'.
    Grade notation: (Grade 1A), (Grade 2B), (Not Graded), (Practice Point).
    """

    def parse(self, path: Path, source: str) -> ParsedDocument:
        doc = ParsedDocument(source=source, path=str(path))
        current_section = ""

        with pdfplumber.open(path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                # Tables — extract data and record bboxes to exclude from text
                table_objects = page.find_tables()
                table_bboxes = [t.bbox for t in table_objects]
                for t_obj in table_objects:
                    raw = t_obj.extract()
                    if raw:
                        flat = " | ".join(
                            " ".join(str(c or "") for c in row) for row in raw
                        )
                        doc.blocks.append(
                            ParsedBlock(
                                text=flat,
                                block_type="table",
                                page_num=page_num,
                                section=current_section,
                                table_data=table_to_dicts(raw),
                                raw_table=raw,
                            )
                        )

                # Reconstruct page text from words NOT inside any table bbox
                all_words = page.extract_words(x_tolerance=3, y_tolerance=3)
                non_table_words = [
                    w for w in all_words
                    if not any(_word_in_bbox(w, bb) for bb in table_bboxes)
                ]
                # Re-group into logical lines by top coordinate
                from .base import group_words_into_lines, words_to_text
                line_groups = group_words_into_lines(non_table_words, y_tolerance=3)
                lines = [words_to_text(lg) for lg in line_groups if words_to_text(lg).strip()]
                pending: list[str] = []
                in_rec = False
                rec_lines: list[str] = []

                def flush_narrative():
                    if not pending:
                        return
                    joined = " ".join(pending).strip()
                    if len(joined) > 4:
                        doc.blocks.append(
                            ParsedBlock(
                                text=joined,
                                block_type="narrative",
                                page_num=page_num,
                                section=current_section,
                            )
                        )
                    pending.clear()

                def flush_rec():
                    if not rec_lines:
                        return
                    full = " ".join(rec_lines).strip()
                    m = _KDIGO_GRADE.search(full)
                    grade = m.group(1) if m else ""
                    clean = _KDIGO_GRADE.sub("", full).strip()
                    if len(clean) > 10:
                        doc.blocks.append(
                            ParsedBlock(
                                text=clean,
                                block_type="recommendation",
                                page_num=page_num,
                                section=current_section,
                                evidence_grade=grade,
                            )
                        )
                    rec_lines.clear()

                # Detect reference-list pages: the header line contains
                # "references" — skip recommendation extraction on those pages
                # entirely (they only contain citation fragments, not guidance).
                _first_line = lines[0].lower() if lines else ""
                _is_reference_page = "references" in _first_line

                for line in lines:
                    if len(line) <= 3:
                        continue

                    # Skip page-header/footer noise lines
                    if (
                        line.startswith("www.kidney-international.org")
                        or line.startswith("KidneyInternational")
                        or line.startswith("Kidney International")
                    ):
                        continue

                    # On reference-list pages only collect narrative, not recs
                    if _is_reference_page:
                        pending.append(line)
                        continue

                    if _ALLCAPS_HEADING.match(line):
                        flush_narrative()
                        flush_rec()
                        in_rec = False
                        current_section = line
                        doc.blocks.append(
                            ParsedBlock(
                                text=line,
                                block_type="heading",
                                page_num=page_num,
                                section=current_section,
                            )
                        )
                        continue

                    # Start of a KDIGO recommendation or practice point
                    if _KDIGO_REC_LABEL.match(line):
                        flush_narrative()
                        flush_rec()
                        in_rec = True
                        rec_lines.append(line)
                        # If the grade is already present on this first line AND
                        # the line ends with a terminal period, it is self-contained
                        if _KDIGO_GRADE.search(line) and line.rstrip().endswith("."):
                            flush_rec()
                            in_rec = False
                        continue

                    # Continuation lines for an open recommendation
                    if in_rec:
                        rec_lines.append(line)
                        # A line ending in a period that follows a grade marker
                        # signals the statement is complete.  We look at the
                        # accumulated text so far rather than just the last line
                        # to handle grade markers that appear on a continuation
                        # line before the final period.
                        accumulated = " ".join(rec_lines)
                        has_grade = bool(_KDIGO_GRADE.search(accumulated))
                        ends_sentence = line.rstrip().endswith(".")
                        # Also close when the next line looks like a new label
                        # (handled at top of loop), or when we have a grade and
                        # a sentence-ending period.
                        if has_grade and ends_sentence:
                            flush_rec()
                            in_rec = False
                        # Safety valve: if we have accumulated a very long
                        # block without a grade/period, close at 8 lines to
                        # avoid swallowing narrative paragraphs.
                        elif len(rec_lines) >= 8:
                            flush_rec()
                            in_rec = False
                        continue

                    pending.append(line)

                flush_narrative()
                flush_rec()

        return doc


# ── IDF-DAR ───────────────────────────────────────────────────────────────────

class IDFDARParser(BaseParser):
    """
    IDF-DAR Practical Guidelines — Diabetes and Ramadan (2021).
    595×842 pt, 333 pages. Sections organised by risk category.
    Recommendations may be bulleted or numbered, occasionally in shaded boxes.
    """

    def parse(self, path: Path, source: str) -> ParsedDocument:
        doc = ParsedDocument(source=source, path=str(path))
        current_section = ""
        risk_context = ""  # very high / high / moderate / low

        with pdfplumber.open(path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                # Tables
                table_objects = page.find_tables()
                for t_obj in table_objects:
                    raw = t_obj.extract()
                    if raw:
                        flat = " | ".join(
                            " ".join(str(c or "") for c in row) for row in raw
                        )
                        doc.blocks.append(
                            ParsedBlock(
                                text=flat,
                                block_type="table",
                                page_num=page_num,
                                section=current_section,
                                table_data=table_to_dicts(raw),
                                raw_table=raw,
                            )
                        )

                text = page.extract_text() or ""
                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                pending: list[str] = []

                def flush():
                    if not pending:
                        return
                    joined = " ".join(pending).strip()
                    if len(joined) > 4:
                        doc.blocks.append(
                            ParsedBlock(
                                text=joined,
                                block_type="narrative",
                                page_num=page_num,
                                section=current_section,
                            )
                        )
                    pending.clear()

                for line in lines:
                    if len(line) <= 3:
                        continue

                    # Risk-category header (Very High Risk, High Risk, …)
                    risk_m = _IDF_RISK_HEADER.search(line)
                    if risk_m:
                        flush()
                        risk_context = risk_m.group(0)
                        current_section = line
                        doc.blocks.append(
                            ParsedBlock(
                                text=line,
                                block_type="heading",
                                page_num=page_num,
                                section=current_section,
                            )
                        )
                        continue

                    if _ALLCAPS_HEADING.match(line) and not _BULLET.match(line):
                        flush()
                        current_section = line
                        doc.blocks.append(
                            ParsedBlock(
                                text=line,
                                block_type="heading",
                                page_num=page_num,
                                section=current_section,
                            )
                        )
                        continue

                    if _BULLET.match(line) or _NUMBERED.match(line):
                        flush()
                        clean = _BULLET.sub("", _NUMBERED.sub("", line)).strip()
                        clean, grade = extract_evidence_grade(clean)
                        # Annotate with risk context if available
                        section_with_risk = (
                            f"{current_section} [{risk_context}]"
                            if risk_context and risk_context not in current_section
                            else current_section
                        )
                        doc.blocks.append(
                            ParsedBlock(
                                text=clean,
                                block_type="recommendation",
                                page_num=page_num,
                                section=section_with_risk,
                                evidence_grade=grade,
                            )
                        )
                        continue

                    pending.append(line)

                flush()

        return doc
