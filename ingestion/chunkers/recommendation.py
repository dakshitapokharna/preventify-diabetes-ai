"""Recommendation chunker — RSSDI, ADA, ICMR_STW, KDIGO, ESC sources.

Splits on <!-- rag_metadata --> comment boundaries. Each comment is the
metadata header for the body text that follows it until the next comment.
"""
from __future__ import annotations

import re
from pathlib import Path

from .base import (
    Chunk, ESC_CLASS_III_HARM, _RAG_META_RE, context_header, make_chunk_id,
    normalize_esc_grade, normalize_grade, parse_rag_metadata, token_estimate,
    split_text_with_ceiling,
)

# ── ESC table-level grade scanner ─────────────────────────────────────────────
_ESC_CLASS_VAL_RE = re.compile(r'^(I{1,3}|IIa|IIb)$', re.IGNORECASE)
_ESC_LEVEL_VAL_RE = re.compile(r'^[ABC]$', re.IGNORECASE)
_MD_TABLE_BLOCK_RE = re.compile(
    r"(\|[^\n]+\|)\n\|[-| :]+\|\n((?:\|[^\n]+\|\n?)*)",
)


def _best_esc_grade_from_table(body: str) -> tuple[str | None, str | None, int]:
    """
    Scan Markdown tables in the chunk body for ESC Class/Level column values.

    ESC recommendation tables use 'Classa' / 'Levelb' headers (footnote suffix)
    and place class/level values in separate rows from the recommendation text,
    e.g.:  |  |  | I | A |  |

    Returns the best (lowest grade_priority) found, or (None, None, 5).
    """
    best_grade = 5
    best_label: str | None = None
    best_schema: str | None = None

    for m in _MD_TABLE_BLOCK_RE.finditer(body):
        header_row = m.group(1)
        data_rows  = m.group(2)

        headers = [h.strip() for h in header_row.strip("|").split("|")]

        # Allow footnote suffixes: 'Classa' matches 'class', 'Levelb' matches 'level'
        class_idx = next(
            (i for i, h in enumerate(headers)
             if "class" in h.lower() and len(h) <= 8),
            None,
        )
        level_idx = next(
            (i for i, h in enumerate(headers)
             if "level" in h.lower() and len(h) <= 8),
            None,
        )
        if class_idx is None or level_idx is None:
            continue

        for row_line in data_rows.strip().splitlines():
            cols = [c.strip() for c in row_line.strip("|").split("|")]
            if len(cols) <= max(class_idx, level_idx):
                continue
            cls_val = cols[class_idx].strip()
            lvl_val = cols[level_idx].strip()
            if not cls_val or not lvl_val:
                continue
            if not _ESC_CLASS_VAL_RE.match(cls_val):
                continue
            if not _ESC_LEVEL_VAL_RE.match(lvl_val):
                continue
            _, schema, prio = normalize_esc_grade(cls_val.upper(), lvl_val.upper())
            if prio < best_grade:
                best_grade = prio
                best_label = f"{cls_val}-{lvl_val}"
                best_schema = schema

    return best_label, best_schema, best_grade

_MAX_TOKENS = 512
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
_SEC_REF_RE = re.compile(r'\b(S\d+[\.\d]*|\d+\.\d[\.\d]*)')
_PAGE_MARKER_RE = re.compile(r"<!--\s*page\s+\d+\s*-->", re.IGNORECASE)


def _nearest_heading(text: str, pos: int) -> tuple[str | None, str | None]:
    """Return (section_ref, section_title) from the last heading before pos."""
    best_title: str | None = None
    for m in _HEADING_RE.finditer(text):
        if m.start() >= pos:
            break
        best_title = m.group(2).strip()
    if best_title is None:
        return None, None
    ref_m = _SEC_REF_RE.search(best_title)
    return (ref_m.group(0) if ref_m else None), best_title


def _split_at_paragraphs(header: str, body: str, base: Chunk) -> list[Chunk]:
    """Split an oversized body using paragraph → sentence → hard-split cascade."""
    fragments = split_text_with_ceiling(header, body, _MAX_TOKENS)

    if len(fragments) <= 1:
        c = Chunk(**base.__dict__.copy())
        c.text = f"{header}\n\n{body}"
        return [c.finalise()]

    chunks: list[Chunk] = []
    total = len(fragments)
    for i, frag in enumerate(fragments, 1):
        c = Chunk(**base.__dict__.copy())
        c.text = f"{header}\n\n{frag}"
        c.fragment = f"{i}/{total}"
        c.chunk_id = make_chunk_id(base.source, base.section_title, c.text)
        chunks.append(c.finalise())
    return chunks


def chunk_recommendation_source(
    md_path: Path,
    source: str,
    year: int,
    retrieval_tier: str,
    condition_trigger: str | None,
    india_specific: bool,
) -> list[Chunk]:
    text = md_path.read_text(encoding="utf-8")
    meta_matches = list(_RAG_META_RE.finditer(text))
    if not meta_matches:
        return []

    all_chunks: list[Chunk] = []

    for i, meta_m in enumerate(meta_matches):
        meta = parse_rag_metadata(meta_m.group(1))

        # Body: from end of this comment to start of next (or EOF)
        body_start = meta_m.end()
        body_end = meta_matches[i + 1].start() if i + 1 < len(meta_matches) else len(text)
        body = text[body_start:body_end]
        # Strip embedded <!-- page N --> markers (ESC extractor leaves these in bodies)
        body = _PAGE_MARKER_RE.sub("", body).strip()

        # Skip empty or heading-only segments
        content_lines = [l for l in body.splitlines()
                         if l.strip() and not l.strip().startswith("#")
                         and not l.strip().startswith("<!--")]
        if not content_lines:
            continue

        # Section context — prefer metadata fields, fall back to nearest heading
        meta_section = meta.get("section", "").strip("\"'")
        sec_ref, sec_title = _nearest_heading(text, meta_m.start())
        section_ref = meta.get("section_ref") or sec_ref
        section_title = meta_section or sec_title

        # Evidence grade — handle "A", "grade_A", and KDIGO "1A" variants
        raw_grade = meta.get("evidence_grade")
        ev_grade, ev_schema, grade_prio = normalize_grade(raw_grade)

        # ESC: _annotate_class_level_inline() emits evidence_class / evidence_level
        # (e.g. evidence_class="Class I" evidence_level="Level A").  Resolve these
        # to a grade_priority via normalize_esc_grade() whenever present and the
        # standard normalize_grade() didn't produce a resolved grade (grade_prio==5).
        raw_cls = meta.get("evidence_class", "").strip()
        raw_lvl = meta.get("evidence_level", "").strip()
        if raw_cls and raw_lvl:
            # Strip "Class " / "Level " prefixes the ESC extractor adds
            cls_key = re.sub(r"(?i)^class\s+", "", raw_cls).strip()
            lvl_key = re.sub(r"(?i)^level\s+", "", raw_lvl).strip()
            ev_grade, ev_schema, grade_prio = normalize_esc_grade(cls_key, lvl_key)

        # ESC recommendation tables: extractor annotates table-level schema only;
        # scan the actual table rows for Class/Level column values to get a real
        # grade_priority instead of the default 5.
        if grade_prio == 5 and meta.get("evidence_schema", "").startswith("ESC"):
            tbl_label, tbl_schema, tbl_prio = _best_esc_grade_from_table(body)
            if tbl_prio < 5:
                ev_grade  = tbl_label
                ev_schema = tbl_schema
                grade_prio = tbl_prio

        # ESC evidence_schema fallback (no separate class/level in metadata)
        if not ev_schema and meta.get("evidence_schema"):
            ev_schema = meta["evidence_schema"]

        # Tags and flags
        tags_raw = meta.get("topic_tags", "")
        topic_tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

        pop_raw = meta.get("population", "")
        population_scope = [p.strip() for p in pop_raw.split() if p.strip()]

        safety_raw = meta.get("safety_critical", meta.get("safety_redline", ""))
        safety_critical = safety_raw.lower() in ("true", "1")

        # ESC Class-III (harm / no benefit) chunks must always be retrieved so the
        # bot knows what NOT to advise.  Mark them safety_critical regardless of the
        # annotation flag — the Class-III schema sentinel is set by normalize_esc_grade().
        if ev_schema == ESC_CLASS_III_HARM:
            safety_critical = True

        chunk_note = meta.get("chunk_note") or None
        kerala_food = meta.get("kerala_food", "").lower() in ("true", "1")
        meal_context = meta.get("meal_context") or None

        keep_atomic = safety_critical or "keep_atomic" in (chunk_note or "")

        header = context_header(source, year, section_ref, section_title)
        full_text = f"{header}\n\n{body}"

        base = Chunk(
            source=source,
            year=year,
            section_ref=section_ref,
            section_title=section_title,
            content_type=meta.get("content_type", "recommendation"),
            evidence_grade=ev_grade,
            evidence_schema=ev_schema,
            grade_priority=grade_prio,
            topic_tags=topic_tags,
            population_scope=population_scope,
            retrieval_tier=retrieval_tier,
            condition_trigger=condition_trigger,
            india_specific=india_specific,
            kerala_food=kerala_food,
            safety_critical=safety_critical,
            chunk_note=chunk_note,
            meal_context=meal_context,
        )

        if keep_atomic or token_estimate(full_text) <= _MAX_TOKENS:
            c = Chunk(**base.__dict__.copy())
            c.text = full_text
            all_chunks.append(c.finalise())
        else:
            all_chunks.extend(_split_at_paragraphs(header, body, base))

    return all_chunks
