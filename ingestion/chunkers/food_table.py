"""Food table chunker — ICMR_NIN only.

Emits two chunk types per food group that has Kerala foods:
  Type A — group-level table chunk (full group, split into 30-row batches if needed)
  Type B — individual Kerala food row chunks (one per row in Kerala sub-tables)

Sources without Kerala foods emit only Type A chunks.
"""
from __future__ import annotations

import re
from pathlib import Path

from .base import (
    Chunk, _RAG_META_RE, context_header, make_chunk_id,
    parse_rag_metadata, token_estimate,
)

_TABLE_HEADER_LINE = "| Food Code | Food Name | Carb (g) | Protein (g) | Fat (g) | Fiber (g) | Energy (kJ) |"
_TABLE_SEP_LINE = "| --- | --- | --- | --- | --- | --- | --- |"
_ROW_BATCH_SIZE = 30
_MAX_TOKENS = 512
_GROUP_HEADING_RE = re.compile(r"^## (.+)$", re.MULTILINE)
_KERALA_HEADING_RE = re.compile(r"^### (.+ — Kerala Relevant Foods)$", re.MULTILINE)


def _parse_table_rows(text: str) -> list[str]:
    """Return data rows (skip header and separator lines)."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("|") and line != _TABLE_SEP_LINE and _TABLE_HEADER_LINE not in line:
            rows.append(line)
    return rows


def _food_name_from_row(row: str) -> str:
    """Extract food name column from a markdown table row."""
    parts = [p.strip() for p in row.split("|") if p.strip()]
    return parts[1] if len(parts) > 1 else ""


def _emit_table_chunks(
    header: str,
    table_header_lines: str,
    rows: list[str],
    base: Chunk,
    batch_size: int = _ROW_BATCH_SIZE,
) -> list[Chunk]:
    """Emit one or more chunks for a table, batching by token count.

    Rows are accumulated greedily until the next row would push the chunk over
    _MAX_TOKENS, at which point a new chunk is started.  batch_size is kept as
    a hard upper bound per chunk to guarantee a minimum of granularity.
    """
    full_table = f"{table_header_lines}\n" + "\n".join(rows)
    full_text = f"{header}\n\n{full_table}"

    if token_estimate(full_text) <= _MAX_TOKENS:
        c = Chunk(**base.__dict__.copy())
        c.text = full_text
        return [c.finalise()]

    # Token-aware batching: accumulate rows until the assembled candidate text would
    # exceed _MAX_TOKENS.  Assemble-and-measure instead of accumulated estimates to
    # avoid floor-division rounding errors that cause silent overruns.
    batches: list[list[str]] = []
    current_batch: list[str] = []

    def _candidate_text(batch: list[str], extra_row: str) -> str:
        tbl = f"{table_header_lines}\n" + "\n".join(batch + [extra_row])
        return f"{header}\n\n{tbl}"

    for row in rows:
        if current_batch and (
            token_estimate(_candidate_text(current_batch, row)) > _MAX_TOKENS
            or len(current_batch) >= batch_size
        ):
            batches.append(current_batch)
            current_batch = [row]
        else:
            current_batch.append(row)

    if current_batch:
        batches.append(current_batch)

    chunks: list[Chunk] = []
    total = len(batches)
    for idx, batch in enumerate(batches, 1):
        batch_table = f"{table_header_lines}\n" + "\n".join(batch)
        c = Chunk(**base.__dict__.copy())
        c.text = f"{header}\n\n{batch_table}"
        c.fragment = f"{idx}/{total}"
        c.chunk_id = make_chunk_id(base.source, base.section_title, c.text)
        chunks.append(c.finalise())
    return chunks


def chunk_food_table_source(
    md_path: Path,
    source: str,
    year: int,
    retrieval_tier: str,
    condition_trigger: str | None,
    india_specific: bool,
) -> list[Chunk]:
    text = md_path.read_text(encoding="utf-8")
    all_chunks: list[Chunk] = []

    table_col_header = f"{_TABLE_HEADER_LINE}\n{_TABLE_SEP_LINE}"

    # Find all group sections (## headings) and Kerala sub-sections (### headings)
    group_positions: list[tuple[int, str]] = []
    for m in _GROUP_HEADING_RE.finditer(text):
        group_positions.append((m.start(), m.group(1).strip()))

    kerala_positions: list[tuple[int, str]] = []
    for m in _KERALA_HEADING_RE.finditer(text):
        kerala_positions.append((m.start(), m.group(1).strip()))

    for g_idx, (g_pos, group_name) in enumerate(group_positions):
        g_end = group_positions[g_idx + 1][0] if g_idx + 1 < len(group_positions) else len(text)
        group_text = text[g_pos:g_end]

        # Find rag_metadata for this group section
        meta_m = _RAG_META_RE.search(group_text)
        meta = parse_rag_metadata(meta_m.group(1)) if meta_m else {}
        kerala_food_flag = meta.get("kerala_food", "").lower() in ("true", "1")
        kerala_foods_str = meta.get("kerala_foods", "")
        tags_raw = meta.get("topic_tags", "food_composition")
        topic_tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

        # ── Type A: full group table ──────────────────────────────────────────
        # Extract the main group table rows (before any ### sub-section)
        kerala_sub_in_group = next(
            (pos for pos, name in kerala_positions if g_pos <= pos < g_end), None
        )
        main_table_end = kerala_sub_in_group if kerala_sub_in_group is not None else g_end
        main_table_text = text[g_pos:main_table_end]
        main_rows = _parse_table_rows(main_table_text)

        if main_rows:
            header = context_header(source, year, None, group_name)
            base_a = Chunk(
                source=source,
                year=year,
                section_title=group_name,
                content_type="table",
                topic_tags=topic_tags,
                retrieval_tier=retrieval_tier,
                condition_trigger=condition_trigger,
                india_specific=india_specific,
                kerala_food=kerala_food_flag,
            )
            all_chunks.extend(_emit_table_chunks(header, table_col_header, main_rows, base_a))

        # ── Type B: individual Kerala food rows ───────────────────────────────
        if kerala_sub_in_group is None:
            continue

        # Kerala sub-section ends at the group boundary (g_end = next ## heading).
        # Do NOT use the next Kerala heading position — that would bleed into other groups.
        kerala_sub_end = g_end
        kerala_section_name = next(
            name for pos, name in kerala_positions if pos == kerala_sub_in_group
        )
        kerala_text = text[kerala_sub_in_group:kerala_sub_end]
        kerala_rows = _parse_table_rows(kerala_text)

        for row in kerala_rows:
            food_name = _food_name_from_row(row)
            # Build single-row chunk
            header_b = context_header(source, year, None, kerala_section_name)
            c = Chunk(
                source=source,
                year=year,
                section_title=kerala_section_name,
                content_type="table",
                topic_tags=topic_tags + ["kerala_food"],
                retrieval_tier=retrieval_tier,
                condition_trigger=condition_trigger,
                india_specific=india_specific,
                kerala_food=True,
                text=f"{header_b}\n\n{table_col_header}\n{row}",
            )
            all_chunks.append(c.finalise())

    return all_chunks
