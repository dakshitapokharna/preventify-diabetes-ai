"""Narrative chunker — Anoop_Misra, WHO_HEARTS, Telemedicine_Guidelines.

Splits at ## / ### heading boundaries; oversized sections are split at
paragraph boundaries with a 50-token overlap window.
"""
from __future__ import annotations

import re
from pathlib import Path

from .base import (
    Chunk, _RAG_META_RE, context_header, make_chunk_id,
    normalize_grade, parse_rag_metadata, token_estimate,
    split_text_with_ceiling,
)

_MAX_TOKENS = 512
_MIN_TOKENS = 50
_OVERLAP_TOKENS = 50
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
_SEC_REF_RE = re.compile(r'\b(S\d+[\.\d]*|\d+\.\d[\.\d]*)')


def _last_n_tokens(text: str, n: int) -> str:
    """Return the last ~n tokens from text, aligned to a word boundary.

    Uses character budget (n * 4 chars) to approximate token count, then
    walks forward to the next word boundary to avoid cutting mid-word.
    More accurate than word-counting because token_estimate() uses len//4.
    """
    max_chars = n * 4
    if len(text) <= max_chars:
        return text
    start_pos = max(0, len(text) - max_chars)
    # Walk forward to next word boundary so we don't start mid-word
    while start_pos < len(text) and text[start_pos] not in (" ", "\n"):
        start_pos += 1
    return text[start_pos:].lstrip()


def _split_with_overlap(header: str, body: str, base: Chunk) -> list[Chunk]:
    """Split oversized narrative body using paragraph → sentence → hard-split cascade.

    Carries a 50-token overlap tail between windows to avoid severing mid-sentence
    clinical claims that bridge paragraph boundaries.

    body_max budget:
    - First chunk (no overlap prepended): _MAX_TOKENS - header_cost - 4
    - Subsequent chunks (overlap prepended): _MAX_TOKENS - header_cost - _OVERLAP_TOKENS - 4
      The overlap tail consumes ~_OVERLAP_TOKENS of the 512-token budget, so the
      real body payload is smaller for all but the first fragment.
    """
    from .base import split_at_sentences, hard_split_text

    header_cost = token_estimate(header + "\n\n")
    # Budget for first chunk (no overlap tail prepended yet)
    body_max_first = max(64, _MAX_TOKENS - header_cost - 4)
    # Budget for subsequent chunks (overlap tail will be prepended)
    body_max_rest = max(64, _MAX_TOKENS - header_cost - _OVERLAP_TOKENS - 4)

    paragraphs = [p.strip() for p in re.split(r"\n\n+", body) if p.strip()]
    if not paragraphs:
        return []

    # Expand any paragraph that is itself over budget via sentence/hard split.
    # Use body_max_rest (the tightest budget) so that any expanded fragment is safe
    # to place in a rest-chunk position where the overlap tail will also be prepended.
    # The first chunk wastes ~50 tokens of headroom, but that's acceptable for safety.
    expanded: list[str] = []
    for para in paragraphs:
        if token_estimate(para) > body_max_rest:
            expanded.extend(split_at_sentences(para, body_max_rest))
        else:
            expanded.append(para)

    chunks: list[Chunk] = []
    current_paras: list[str] = []
    overlap_tail = ""

    def _assemble(paras: list[str], tail: str) -> str:
        """Assemble the full candidate chunk text for accurate token measurement."""
        body = "\n\n".join(paras)
        if tail:
            body = tail + "\n\n" + body
        return f"{header}\n\n{body.strip()}"

    for para in expanded:
        # Measure the assembled candidate text — avoids floor-division rounding error
        # that occurs when summing token_estimate() across fragments.
        candidate_text = _assemble(current_paras + [para], overlap_tail if chunks else "")
        if token_estimate(candidate_text) > _MAX_TOKENS and current_paras:
            # Flush current window
            body_text_assembled = _assemble(current_paras, overlap_tail if chunks else "")
            c = Chunk(**base.__dict__.copy())
            c.text = body_text_assembled
            c.chunk_id = make_chunk_id(base.source, base.section_title, c.text)
            chunks.append(c.finalise())

            overlap_tail = _last_n_tokens("\n\n".join(current_paras), _OVERLAP_TOKENS)
            current_paras = [para]
        else:
            current_paras.append(para)

    if current_paras:
        c = Chunk(**base.__dict__.copy())
        c.text = _assemble(current_paras, overlap_tail if chunks else "")
        # Bug fix: was using base.section_ref; must use section_title for stable IDs
        c.chunk_id = make_chunk_id(base.source, base.section_title, c.text)
        chunks.append(c.finalise())

    if len(chunks) > 1:
        total = len(chunks)
        for idx, ch in enumerate(chunks, 1):
            ch.fragment = f"{idx}/{total}"
    return chunks


def _section_metadata(section_text: str) -> dict[str, str]:
    """Extract the last rag_metadata comment from a section's text."""
    matches = list(_RAG_META_RE.finditer(section_text))
    if not matches:
        return {}
    return parse_rag_metadata(matches[-1].group(1))


def chunk_narrative_source(
    md_path: Path,
    source: str,
    year: int,
    retrieval_tier: str,
    condition_trigger: str | None,
    india_specific: bool,
) -> list[Chunk]:
    text = md_path.read_text(encoding="utf-8")

    # Split into sections at heading boundaries
    heading_positions: list[tuple[int, int, str]] = []  # (start, level, title)
    for m in _HEADING_RE.finditer(text):
        heading_positions.append((m.start(), len(m.group(1)), m.group(2).strip()))

    if not heading_positions:
        # No headings — treat whole file as one section
        heading_positions = [(0, 1, source)]

    # Build section slices
    sections: list[tuple[int, str, int, str]] = []  # (level, title, start, end)
    for idx, (pos, level, title) in enumerate(heading_positions):
        content_start = text.index("\n", pos) + 1 if "\n" in text[pos:] else pos
        content_end = heading_positions[idx + 1][0] if idx + 1 < len(heading_positions) else len(text)
        sections.append((level, title, content_start, content_end))

    all_chunks: list[Chunk] = []
    pending_meta: dict[str, str] = {}  # inherited from nearest parent section

    for level, title, start, end in sections:
        section_body = text[start:end]
        meta = _section_metadata(section_body)
        if not meta:
            meta = pending_meta.copy()
        else:
            pending_meta = meta.copy()

        # Strip rag_metadata comments from body text before chunking
        body = _RAG_META_RE.sub("", section_body).strip()
        if not body or token_estimate(body) < _MIN_TOKENS:
            continue

        # Section ref extraction
        ref_m = _SEC_REF_RE.search(title)
        section_ref = meta.get("section_ref") or (ref_m.group(0) if ref_m else None)
        section_title = meta.get("section", "").strip("\"'") or title

        # Evidence/flags
        raw_grade = meta.get("evidence_grade")
        ev_grade, ev_schema, grade_prio = normalize_grade(raw_grade)
        if not ev_schema and meta.get("evidence_schema"):
            ev_schema = meta["evidence_schema"]

        tags_raw = meta.get("topic_tags", "")
        topic_tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

        pop_raw = meta.get("population", "")
        population_scope = [p.strip() for p in pop_raw.split() if p.strip()]

        safety_raw = meta.get("safety_critical", meta.get("safety_redline", ""))
        safety_critical = safety_raw.lower() in ("true", "1")
        chunk_note = meta.get("chunk_note") or None

        header = context_header(source, year, section_ref, section_title)

        base = Chunk(
            source=source,
            year=year,
            section_ref=section_ref,
            section_title=section_title,
            content_type="narrative",
            evidence_grade=ev_grade,
            evidence_schema=ev_schema,
            grade_priority=grade_prio,
            topic_tags=topic_tags,
            population_scope=population_scope,
            retrieval_tier=retrieval_tier,
            condition_trigger=condition_trigger,
            india_specific=india_specific,
            safety_critical=safety_critical,
            chunk_note=chunk_note,
        )

        full_text = f"{header}\n\n{body}"
        keep_atomic = safety_critical or "keep_atomic" in (chunk_note or "")

        if keep_atomic or token_estimate(full_text) <= _MAX_TOKENS:
            c = Chunk(**base.__dict__.copy())
            c.text = full_text
            all_chunks.append(c.finalise())
        else:
            frags = _split_with_overlap(header, body, base)
            all_chunks.extend(frags)

    return all_chunks
