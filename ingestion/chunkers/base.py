"""Shared types, helpers, and evidence normalization for all chunkers."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

_logger = logging.getLogger(__name__)


# ── Evidence grade normalization (CHUNKING_LOGIC.md) ─────────────────────────

# Sentinel schema name for ESC Class-III (harm / no benefit) recommendations.
# The recommendation chunker detects this and sets safety_critical=True so
# these chunks are always retrieved — the bot must know what NOT to advise.
ESC_CLASS_III_HARM = "ESC_Class_III_HARM"

EVIDENCE_NORMALIZATION: dict[str, tuple[str, str, int]] = {
    # ADA / RSSDI schema  (A = strongest evidence, E = expert opinion)
    "A":  ("A",  "ADA_RSSDI_ABCE", 1),
    "B":  ("B",  "ADA_RSSDI_ABCE", 2),
    "C":  ("C",  "ADA_RSSDI_ABCE", 3),
    "E":  ("E",  "ADA_RSSDI_ABCE", 4),

    # KDIGO schema — two-axis grading:
    #   first digit  = recommendation strength  (1 = "We recommend" / strong,
    #                                            2 = "We suggest"   / weak)
    #   letter       = evidence quality          (A = high, B = moderate,
    #                                            C = low,  D = very low)
    # Priority encodes BOTH dimensions: strong recs always rank higher than
    # weak recs at the same evidence quality level.
    # E.g. 1B (strong/moderate) = 2 > 2A (weak/high) = 3.
    "1A": ("1A", "KDIGO_grading", 1),  # strong rec, high evidence
    "1B": ("1B", "KDIGO_grading", 2),  # strong rec, moderate evidence
    "1C": ("1C", "KDIGO_grading", 3),  # strong rec, low evidence
    "1D": ("1D", "KDIGO_grading", 4),  # strong rec, very low evidence
    "2A": ("2A", "KDIGO_grading", 3),  # weak rec, high evidence   ← 3 (not 2)
    "2B": ("2B", "KDIGO_grading", 3),  # weak rec, moderate evidence
    "2C": ("2C", "KDIGO_grading", 4),  # weak rec, low evidence
    "2D": ("2D", "KDIGO_grading", 5),  # weak rec, very low evidence
}


def normalize_esc_grade(evidence_class: str, evidence_level: str) -> tuple[str, str, int]:
    """Resolve ESC Class (I / IIa / IIb / III) × Level (A / B / C) to a priority integer.

    Priority formula for Class I / IIa / IIb:
        class_priority: I=1, IIa=2, IIb=3
        level_modifier: A/B=+0, C=+1
        combined = class_priority + level_modifier, capped at 5

    Class-III special rule:
        ESC Class-III means the intervention is NOT recommended or potentially harmful.
        These always get priority=5 AND schema=ESC_CLASS_III_HARM.
        The recommendation chunker sets safety_critical=True for all Class-III chunks
        so they are always retrieved — the bot must know what not to advise.

    Case-insensitive: "IIa", "IIA", "iia" are all treated identically.
    Unknown class/level → priority 5 (consistent with all other unknown fallbacks).
    """
    cls = evidence_class.upper().strip()
    lvl = evidence_level.upper().strip()
    merged = f"{evidence_class}-{evidence_level}"

    # Class-III = harm / no benefit — not a grading tier, a contraindication signal
    if cls == "III":
        return merged, ESC_CLASS_III_HARM, 5

    # IIa / IIb come in mixed case ("IIa") or all-upper ("IIA") depending on caller
    class_priority = {"I": 1, "IIA": 2, "IIB": 3}
    level_modifier = {"A": 0, "B": 0, "C": 1}

    # Unknown class → 5 (was 4 — inconsistent with every other unknown fallback)
    priority = class_priority.get(cls, 5) + level_modifier.get(lvl, 1)
    return merged, "ESC_Class_Level", min(int(priority), 5)


CONSENSUS_GRADE: tuple[str, str, int] = ("consensus", "source_consensus", 5)


def normalize_grade(raw: str | None) -> tuple[str | None, str | None, int]:
    """Map a raw grade string from any source to (label, schema, grade_priority).

    Handles:
    - ADA/RSSDI: "A", "B", "C", "E"  (also "grade_A" prefix form)
    - KDIGO:     "1A", "1B", … "2D"
    - Unknown:   returned as-is with schema=None and priority=5; a warning is logged
                 so corpus engineers can catch extractor annotation gaps early.
    """
    if not raw:
        return None, None, 5
    # Strip "grade_" prefix used by some extractors (e.g. ADA: "grade_A" → "A")
    key = raw.strip()
    if key.lower().startswith("grade_"):
        key = key[6:]
    key = key.upper()
    if key in EVIDENCE_NORMALIZATION:
        g, schema, priority = EVIDENCE_NORMALIZATION[key]
        return g, schema, priority
    # Unrecognised grade string — log so extractor annotation gaps surface early
    _logger.warning(
        "normalize_grade: unrecognised grade %r → assigned priority 5 (check extractor annotation)",
        raw.strip(),
    )
    return raw.strip(), None, 5


# ── Deterministic chunk ID ─────────────────────────────────────────────────────
def make_chunk_id(source: str, section_title: str | None, text: str) -> str:
    """Stable, deterministic 16-char hex ID.

    Uses section_title (not section_ref) per CHUNKING_LOGIC.md spec.
    text[:500] instead of text[:200] — ICMR-NIN Type B headers alone
    exceed 210 chars; 500 chars guarantees the food-row data is included
    in the fingerprint, preventing hash collisions across rows in the same group.
    """
    ref = section_title or "nosec"
    fingerprint = f"{source}|{ref}|{text[:500]}"
    return hashlib.sha256(fingerprint.encode()).hexdigest()[:16]


def make_text_hash(text: str) -> str:
    """Hash the chunk body only (excluding the context header line).

    The context header is source- and section-specific, so hashing the full text
    would produce different hashes for identical recommendations from different
    sources — defeating the cross-source deduplication at upsert time.
    The body starts after the first '\\n\\n' (blank line after the header line).
    """
    sep_pos = text.find("\n\n")
    body = text[sep_pos + 2:].strip() if sep_pos != -1 else text.strip()
    return hashlib.sha256(body.encode()).hexdigest()[:32]


def token_estimate(text: str) -> int:
    return max(1, len(text) // 4)


# ── Text splitting helpers ─────────────────────────────────────────────────────

_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z\d\"\'])')


def split_at_sentences(text: str, max_tokens: int = 512) -> list[str]:
    """Split text at sentence boundaries when a paragraph exceeds max_tokens.

    Falls back to hard character-split when no sentence boundaries exist
    (e.g. RSSDI pdfplumber word-concatenation artifacts).
    Any fragment that is still > max_tokens is hard-split recursively.
    """
    sentences = _SENTENCE_SPLIT_RE.split(text)
    if len(sentences) <= 1:
        # No sentence boundaries — hard-split at word boundaries
        return hard_split_text(text, max_tokens)

    raw: list[str] = []
    current = ""
    for sent in sentences:
        candidate = (current + " " + sent).strip() if current else sent
        if current and token_estimate(candidate) > max_tokens:
            raw.append(current.strip())
            current = sent
        else:
            current = candidate
    if current.strip():
        raw.append(current.strip())

    # Ensure no single fragment exceeds max_tokens (single long sentences)
    result: list[str] = []
    for frag in (raw if raw else [text]):
        if token_estimate(frag) > max_tokens:
            result.extend(hard_split_text(frag, max_tokens))
        else:
            result.append(frag)
    return result if result else [text]


def hard_split_text(text: str, max_tokens: int = 512) -> list[str]:
    """Last-resort split: break at word boundaries up to max_tokens characters.

    Used when text has no sentence or paragraph delimiters (e.g. concatenated
    pdfplumber output like 'InternationalJournalofDiabetes...').
    """
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return [text]
    fragments: list[str] = []
    while text:
        if len(text) <= max_chars:
            fragments.append(text)
            break
        # Try to split at last space before the ceiling
        split_at = text.rfind(" ", 0, max_chars)
        if split_at <= 0:
            split_at = max_chars  # no space or space at pos 0 — hard cut
        fragment = text[:split_at].strip()
        if fragment:  # guard against empty fragment when rfind returns 0
            fragments.append(fragment)
        text = text[split_at:].strip()
    return fragments


def split_text_with_ceiling(
    header: str,
    body: str,
    max_tokens: int = 512,
) -> list[str]:
    """Cascade: paragraph → sentence → hard split until all fragments ≤ max_tokens.

    Returns a list of body fragments (header NOT prepended — caller does that).
    The effective body budget is max_tokens minus the header token cost so that
    the full chunk (header + body) always fits within max_tokens.
    """
    header_cost = token_estimate(header + "\n\n")
    # Subtract header cost + 4-token rounding buffer so full chunk fits within max_tokens
    body_max = max(64, max_tokens - header_cost - 4)

    paragraphs = [p.strip() for p in re.split(r"\n\n+", body) if p.strip()]

    fragments: list[str] = []
    current = ""

    for para in paragraphs:
        candidate = (current + "\n\n" + para).strip() if current else para
        if current and token_estimate(candidate) > body_max:
            fragments.append(current)
            current = para
        else:
            current = candidate

    if current:
        fragments.append(current)

    # Second pass: break any fragment that is still over the body budget
    final: list[str] = []
    for frag in fragments:
        if token_estimate(frag) > body_max:
            sub = split_at_sentences(frag, body_max)
            final.extend(sub)
        else:
            final.append(frag)

    return [f for f in final if f.strip()]


# ── rag_metadata comment parser ───────────────────────────────────────────────
# Handles: key=value  key="value with spaces"  key='value'
_KV_RE = re.compile(r'(\w+)=(?:"([^"]*?)"|\'([^\']*?)\'|([^\s>]+))')
_RAG_META_RE = re.compile(r"<!--\s*rag_metadata\b(.*?)-->", re.DOTALL)


def parse_rag_metadata(comment_body: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for m in _KV_RE.finditer(comment_body):
        key = m.group(1)
        val = m.group(2) if m.group(2) is not None else (
              m.group(3) if m.group(3) is not None else (m.group(4) or ""))
        result[key] = val
    return result


# ── Chunk dataclass ───────────────────────────────────────────────────────────
@dataclass
class Chunk:
    chunk_id: str = ""
    source: str = ""
    year: int = 0
    section_ref: Optional[str] = None
    section_title: Optional[str] = None
    text: str = ""
    content_type: str = "narrative"
    evidence_grade: Optional[str] = None
    evidence_schema: Optional[str] = None
    grade_priority: int = 5
    topic_tags: list[str] = field(default_factory=list)
    population_scope: list[str] = field(default_factory=list)
    age_scope: Optional[str] = None
    retrieval_tier: str = "core"
    condition_trigger: Optional[str] = None
    india_specific: bool = True
    kerala_food: bool = False
    safety_critical: bool = False
    chunk_note: Optional[str] = None
    meal_context: Optional[str] = None
    page_range: Optional[str] = None
    fragment: Optional[str] = None
    duplicate_of: Optional[str] = None
    token_estimate: int = 0
    char_count: int = 0
    text_hash: str = ""

    def finalise(self) -> "Chunk":
        self.token_estimate = token_estimate(self.text)
        self.char_count = len(self.text)
        self.text_hash = make_text_hash(self.text)
        if not self.chunk_id:
            self.chunk_id = make_chunk_id(self.source, self.section_title, self.text)
        return self

    # The 14 fields that go to JSONL (CHUNKING_LOGIC.md spec).
    # All other dataclass fields are internal-only (chunker bookkeeping,
    # evidence intermediates, dedup hints) and are intentionally excluded.
    _OUTPUT_FIELDS: frozenset = frozenset({
        "chunk_id", "source", "year", "section_title", "text",
        "retrieval_tier", "condition_trigger", "india_specific",
        "kerala_food", "safety_critical", "grade_priority",
        "meal_context", "text_hash", "token_estimate",
    })

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k in self._OUTPUT_FIELDS}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def context_header(source: str, year: int, section_ref: str | None, section_title: str | None) -> str:
    src_label = f"{source} {year}"
    if section_ref and section_title:
        return f"[{src_label} — {section_ref}: {section_title}]"
    if section_title:
        return f"[{src_label} — {section_title}]"
    return f"[{src_label}]"
