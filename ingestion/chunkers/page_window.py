"""Page-window chunker — IDF_DAR and KDIGO_2022_DM_CKD.

Two-pass strategy (CHUNKING_LOGIC.md §3):
  Pass 1 — extract priority chunks (safety_redline, meal_context,
            keep_atomic_large_window annotations) as standalone atomics.
  Pass 2 — slide a 2-page window with 1-page overlap over all remaining text.
"""
from __future__ import annotations

import re
from pathlib import Path

from .base import (
    Chunk, _RAG_META_RE, context_header, make_chunk_id,
    parse_rag_metadata, token_estimate, split_text_with_ceiling,
)

_PAGE_RE = re.compile(r"<!--\s*page\s+(\d+)\s*-->")
_CONTEXT_LINES_AFTER = 4    # lines of context to include after a priority annotation
_CONTEXT_LINES_BEFORE = 0   # lines before (annotation is already mid-sentence; skip)
_MAX_TOKENS = 512


def _page_slices(text: str) -> list[tuple[int, str]]:
    """Return list of (page_num, page_text) pairs."""
    markers = list(_PAGE_RE.finditer(text))
    if not markers:
        return [(1, text)]
    pages: list[tuple[int, str]] = []
    for i, m in enumerate(markers):
        page_num = int(m.group(1))
        content_start = m.end()
        content_end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        page_text = text[content_start:content_end]
        pages.append((page_num, page_text))
    return pages


def _emit_window_chunks(
    header: str,
    window_text: str,
    base_kwargs: dict,
    page_range: str,
) -> list[Chunk]:
    """Emit one or more chunks for a page window, enforcing the 512-token ceiling.

    If window_text fits in one chunk it is emitted as-is.  If it overflows the
    ceiling the text is split via paragraph → sentence → hard-split cascade so
    every output chunk stays within the limit.
    """
    full_text = f"{header}\n\n{window_text}"
    if token_estimate(full_text) <= _MAX_TOKENS:
        c = Chunk(**base_kwargs)
        c.text = full_text
        c.page_range = page_range
        return [c.finalise()]

    fragments = split_text_with_ceiling(header, window_text, _MAX_TOKENS)
    if not fragments:
        return []

    chunks: list[Chunk] = []
    total = len(fragments)
    for i, frag in enumerate(fragments, 1):
        c = Chunk(**base_kwargs)
        c.text = f"{header}\n\n{frag}"
        c.page_range = page_range
        if total > 1:
            c.fragment = f"{i}/{total}"
            c.chunk_id = make_chunk_id(
                base_kwargs["source"], base_kwargs.get("section_title"), c.text
            )
        chunks.append(c.finalise())
    return chunks


def _is_priority(meta: dict) -> tuple[bool, str]:
    """Return (is_priority, priority_type)."""
    if meta.get("safety_redline", "").lower() in ("true", "1"):
        return True, "safety_redline"
    if meta.get("safety_critical", "").lower() in ("true", "1"):
        return True, "safety_critical"
    if meta.get("meal_context"):
        return True, "meal_context"
    if meta.get("chunk_note", "") == "keep_atomic_large_window":
        return True, "risk_stratification"
    return False, ""


def chunk_page_window_source(
    md_path: Path,
    source: str,
    year: int,
    retrieval_tier: str,
    condition_trigger: str | None,
    india_specific: bool,
) -> list[Chunk]:
    text = md_path.read_text(encoding="utf-8")
    all_chunks: list[Chunk] = []

    # Track which line indices are consumed by priority chunks
    lines = text.splitlines(keepends=True)
    consumed: set[int] = set()

    # ── Pass 1: priority chunks ───────────────────────────────────────────────
    for meta_m in _RAG_META_RE.finditer(text):
        meta = parse_rag_metadata(meta_m.group(1))
        is_prio, prio_type = _is_priority(meta)
        if not is_prio:
            continue

        # Find which line this comment ends on
        comment_end_pos = meta_m.end()
        comment_line_idx = text[:comment_end_pos].count("\n")

        # Grab body: from end of comment to next rag_metadata or page marker or +5 lines
        body_start = meta_m.end()
        # Find end of priority block
        next_meta = _RAG_META_RE.search(text, body_start)
        next_page = _PAGE_RE.search(text, body_start)
        candidates = [pos for pos in [
            next_meta.start() if next_meta else None,
            next_page.start() if next_page else None,
        ] if pos is not None]

        # Also limit to ~5 lines after comment
        body_lines_start = comment_line_idx + 1
        body_lines_end = min(body_lines_start + _CONTEXT_LINES_AFTER + 1, len(lines))
        five_line_pos = sum(len(l) for l in lines[:body_lines_end])

        if candidates:
            body_end = min(*candidates, five_line_pos)
        else:
            body_end = five_line_pos

        body = text[body_start:body_end].strip()
        if not body:
            continue

        # Mark lines consumed
        for li in range(comment_line_idx, body_lines_end):
            consumed.add(li)

        content_type_map = {
            "safety_redline": "safety_threshold",
            "safety_critical": "safety_threshold",
            "meal_context": "meal_timing",
            "risk_stratification": "risk_stratification",
        }

        tags_raw = meta.get("topic_tags", "")
        topic_tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        meal_ctx = meta.get("meal_context") or None
        pop_raw = meta.get("population", "")
        population_scope = [p.strip() for p in pop_raw.split() if p.strip()]

        header = context_header(source, year, None, prio_type.replace("_", " ").title())
        c = Chunk(
            source=source,
            year=year,
            content_type=content_type_map.get(prio_type, "recommendation"),
            topic_tags=topic_tags,
            population_scope=population_scope,
            retrieval_tier=retrieval_tier,
            condition_trigger=condition_trigger,
            india_specific=india_specific,
            safety_critical=prio_type in ("safety_redline", "safety_critical"),
            chunk_note=meta.get("chunk_note") or None,
            meal_context=meal_ctx,
            text=f"{header}\n\n{body}",
        )
        all_chunks.append(c.finalise())

    # ── Pass 2: 2-page sliding window over remaining text ────────────────────
    pages = _page_slices(text)
    if len(pages) < 2:
        # Single block — emit with ceiling enforcement
        remaining = "\n".join(
            l.rstrip("\n") for i, l in enumerate(lines) if i not in consumed
        ).strip()
        if remaining:
            header = context_header(source, year, None, None)
            base_kwargs = dict(
                source=source, year=year, content_type="narrative",
                retrieval_tier=retrieval_tier, condition_trigger=condition_trigger,
                india_specific=india_specific,
            )
            all_chunks.extend(_emit_window_chunks(header, remaining, base_kwargs, "1"))
        return all_chunks

    # Build page texts with consumed lines removed
    # Re-parse with consumed tracking: rough approach — consume by character position
    # For simplicity, rebuild page texts by stripping lines that contain priority annotations
    priority_line_set = {
        li for li in consumed
    }

    clean_pages: list[tuple[int, str]] = []
    for page_num, page_text in pages:
        clean_lines = []
        for line in page_text.splitlines():
            # Drop lines that are rag_metadata comments (already emitted as priority)
            if _RAG_META_RE.search(line):
                continue
            clean_lines.append(line)
        clean_pages.append((page_num, "\n".join(clean_lines)))

    # Slide window: window = pages[i] + pages[i+1]; overlap = share pages[i+1] with next window
    base_kwargs = dict(
        source=source, year=year, content_type="narrative",
        retrieval_tier=retrieval_tier, condition_trigger=condition_trigger,
        india_specific=india_specific,
    )
    i = 0
    while i < len(clean_pages):
        p1_num, p1_text = clean_pages[i]
        if i + 1 < len(clean_pages):
            p2_num, p2_text = clean_pages[i + 1]
            window_text = (p1_text + "\n" + p2_text).strip()
            page_range = f"{p1_num}–{p2_num}"
            step = 1  # 1-page overlap: advance by 1
        else:
            window_text = p1_text.strip()
            page_range = str(p1_num)
            step = 1

        if window_text:
            header = context_header(source, year, None, f"Pages {page_range}")
            all_chunks.extend(
                _emit_window_chunks(header, window_text, base_kwargs, page_range)
            )
        i += step

    return all_chunks
