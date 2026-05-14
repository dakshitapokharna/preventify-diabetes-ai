"""
Narrative / policy document parser.

Handles single-column documents with flowing prose and occasional tables:
  - WHO HEARTS Technical Package   — 595×842 pt, 12 pages
  - Telemedicine Practice Guidelines India 2020 — 595×842 pt, 48 pages

Both are A4-sized government/WHO publications with no multi-column layout.

Strategy
────────
1. Extract tables atomically.
2. Extract remaining text with extract_text(); split into paragraphs.
3. Classify each paragraph: heading (ALL CAPS, short) or narrative.
4. Accumulate consecutive narrative lines into coherent paragraph blocks
   so each block represents one thought, not one line.
"""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from .base import BaseParser, ParsedBlock, ParsedDocument, table_to_dicts

_ALLCAPS_HEADING = re.compile(r"^[A-Z][A-Z\s\(\)\-\/&]{4,}$")
_MIN_LEN = 4
_PARA_BREAK_LINES = 2   # consecutive blank lines → paragraph boundary


class NarrativeParser(BaseParser):
    """Parser for single-column narrative/policy PDFs."""

    def parse(self, path: Path, source: str) -> ParsedDocument:
        doc = ParsedDocument(source=source, path=str(path))
        current_section = ""

        with pdfplumber.open(path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                # Tables
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

                text = page.extract_text() or ""
                lines = text.splitlines()

                pending: list[str] = []

                def flush():
                    if not pending:
                        return
                    joined = " ".join(l for l in pending if l).strip()
                    if len(joined) >= _MIN_LEN:
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
                    stripped = line.strip()

                    # Blank line → paragraph break
                    if not stripped:
                        flush()
                        continue

                    if len(stripped) < _MIN_LEN:
                        continue

                    # Section heading
                    if _ALLCAPS_HEADING.match(stripped):
                        flush()
                        current_section = stripped
                        doc.blocks.append(
                            ParsedBlock(
                                text=stripped,
                                block_type="heading",
                                page_num=page_num,
                                section=current_section,
                            )
                        )
                        continue

                    # Bullet-like line → own paragraph block
                    if stripped.startswith(("•", "●", "◆", "–", "-", "▶")):
                        flush()
                        pending.append(stripped.lstrip("•●◆–-▶ "))
                        flush()
                        continue

                    pending.append(stripped)

                flush()

        return doc
