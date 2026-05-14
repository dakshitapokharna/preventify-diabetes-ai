"""
Workflow / flowchart parser.

Handles:  ICMR STW 2024 — Standard Treatment Workflow for T2DM
Page size: 842 × 1634 pt (single very tall page, a clinical decision flowchart)
Tables detected: 2 (the structured treatment decision tables embedded in the chart)

Strategy
────────
1. Extract both embedded tables as table blocks (these are the high-value content).
2. Extract all remaining text grouped into lines; emit as narrative blocks.
   Flow arrows and connector text are included as-is — the chunker can filter later.
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from .base import (
    BaseParser,
    ParsedBlock,
    ParsedDocument,
    group_words_into_lines,
    table_to_dicts,
    words_to_text,
)

_MIN_TEXT_LEN = 4


class ICMRWorkflowParser(BaseParser):
    """Parser for the ICMR STW 2024 single-page workflow document."""

    def parse(self, path: Path, source: str) -> ParsedDocument:
        doc = ParsedDocument(source=source, path=str(path))

        with pdfplumber.open(path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                table_objects = page.find_tables()
                table_bboxes = [t.bbox for t in table_objects]

                # Extract tables first
                for t_obj in table_objects:
                    raw = t_obj.extract()
                    if not raw:
                        continue
                    flat = " | ".join(
                        " ".join(str(c or "") for c in row) for row in raw
                    )
                    doc.blocks.append(
                        ParsedBlock(
                            text=flat,
                            block_type="table",
                            page_num=page_num,
                            section="Treatment Workflow",
                            table_data=table_to_dicts(raw),
                            raw_table=raw,
                        )
                    )

                # Non-table words → narrative lines
                all_words = page.extract_words(x_tolerance=3, y_tolerance=3)
                content_words = [
                    w for w in all_words
                    if not any(_overlaps(w, bb) for bb in table_bboxes)
                ]

                lines = group_words_into_lines(content_words, y_tolerance=4)
                current_section = "Treatment Workflow"

                for line_words in lines:
                    text = words_to_text(line_words)
                    if len(text) < _MIN_TEXT_LEN:
                        continue
                    doc.blocks.append(
                        ParsedBlock(
                            text=text,
                            block_type="narrative",
                            page_num=page_num,
                            section=current_section,
                        )
                    )

        return doc


def _overlaps(word: dict, bbox: tuple) -> bool:
    wx0, wtop, wx1, wbot = word["x0"], word["top"], word["x1"], word["bottom"]
    tx0, ttop, tx1, tbot = bbox
    return not (wx1 < tx0 or wx0 > tx1 or wbot < ttop or wtop > tbot)
