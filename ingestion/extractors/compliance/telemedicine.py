"""
Telemedicine Practice Guidelines of India 2020 — compliance namespace extractor.

Backend: pdfplumber via NarrativeParser (NOT Docling).
Reason: Docling's VLM layout model throws std::bad_alloc on pages 15–48 of this
PDF and skips them entirely — the drug annexure and all flowcharts vanish.
NarrativeParser was written specifically for this document (single-column A4,
48 pages) and extracts all 48 pages cleanly.

Three structure types that a basic parser destroys — and how this extractor
preserves them:

  1. Drug prescription lists (List O / List A / List B)
     Each list head appears as a bullet item ("- List O : ..."), not an ATX
     heading. _annotate_drug_lists() matches the inline bullet form and prepends
     a rag_metadata comment binding each item to its prescribing constraint so
     chunks never lose the list-type context.

  2. Decision flowcharts (Section 7, pages 35–42)
     Docling renders flowchart images as blank <!-- image --> placeholders.
     pdfplumber extracts the text labels inside the boxes and arrows so the
     step-by-step logic is preserved as sequential prose. _annotate_consultation_
     flows() then tags lines with consultation-mode keywords so retrieval can
     surface the correct decision rule without crossing into penalty sections.

  3. RMP duty / penalty clauses
     _inject_section_metadata() attaches topic_tags (RMP_duties, penalties,
     code_of_conduct) to every substantive heading so clause-level chunks inherit
     section context.

Output: parsed/Telemedicine_Guidelines_India_2020.md

Usage:
    python ingestion/extractors/compliance/telemedicine.py
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent.parent.parent
PDF_PATH = ROOT / "corpus/compliance/Telemedicine_Practice_Guidelines_India_2020.pdf"
OUT_DIR = ROOT / "parsed"
OUT_FILE = OUT_DIR / "Telemedicine_Guidelines_India_2020.md"

SOURCE_KEY = "Telemedicine_Guidelines_India_2020"
CITATION = (
    "Telemedicine Practice Guidelines: Enabling Registered Medical Practitioners to "
    "Provide Healthcare Using Telemedicine. Ministry of Health & Family Welfare, "
    "Board of Governors in supersession of MCI, NITI Aayog. March 2020."
)
YEAR = 2020

# ── RAG document-level header ─────────────────────────────────────────────────
RAG_HEADER = f"""\
<!-- rag_metadata
  source: {SOURCE_KEY}
  title: Telemedicine Practice Guidelines — India 2020
  citation: {CITATION}
  year: {YEAR}
  population: All patients receiving telemedicine consultations in India
  topic_tags: telemedicine, RMP_duties, prescribing_rules, drug_lists, consent_framework,
              consultation_modes, scope_boundary, penalties, compliance, SaMD
  retrieval_tier: compliance
  condition_trigger: null
  india_specific: true
  namespace: compliance
  age_scope: all
-->

# Telemedicine Practice Guidelines — India 2020

**Source:** {SOURCE_KEY}
**Citation:** {CITATION}
**Namespace:** compliance — queried silently to enforce scope boundaries; not shown to patients.
**Scope:** Legal framework governing telemedicine consultations by Registered Medical
Practitioners (RMPs) in India. Covers: eligible practitioners and patients, consent,
consultation modes (text/audio/video), prescribing categories (List O/A/B),
prohibited prescriptions, code of conduct, and penalties for violations.

> **System use note:** This document defines what the AI educator can and cannot do
> under Indian telemedicine law. It is the primary authority for scope-boundary
> enforcement. Drug prescribing categories here override any inferred prescribing
> behaviour from clinical corpus sources.

---
"""

# ── Section-level metadata map ────────────────────────────────────────────────
SECTION_TAG_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"List\s+O\b|over.the.counter|OTC", re.I),
     "drug_list_O, OTC_prescribing, prescribing_category"),
    (re.compile(r"List\s+A\b", re.I),
     "drug_list_A, prescribing_category, restricted_prescribing"),
    (re.compile(r"List\s+B\b", re.I),
     "drug_list_B, prescribing_category, video_only_prescribing"),
    (re.compile(r"prohibit|not.*prescri|cannot.*prescri|narcotic|psychotropic|Schedule\s+[XHGH1]", re.I),
     "prohibited_drugs, prescribing_restrictions, controlled_substances"),
    (re.compile(r"consent|informed consent|patient.*agree", re.I),
     "consent_framework, patient_rights, implied_consent"),
    (re.compile(r"text.only|text.based|messaging|chat|asynchronous", re.I),
     "text_consultation, consultation_mode, asynchronous"),
    (re.compile(r"audio|voice|telephone|phone|call", re.I),
     "audio_consultation, consultation_mode"),
    (re.compile(r"video|live.video|real.time|synchronous", re.I),
     "video_consultation, consultation_mode, synchronous"),
    (re.compile(r"first.*consult|new.*patient|initial.*visit", re.I),
     "first_consultation, new_patient, consultation_workflow"),
    (re.compile(r"follow.up|subsequent|established.*patient", re.I),
     "follow_up_consultation, established_patient"),
    (re.compile(r"RMP|registered medical|practitioner|doctor|physician", re.I),
     "RMP_duties, practitioner_obligations"),
    (re.compile(r"duty|obligation|responsibility|code of conduct|ethics", re.I),
     "RMP_duties, code_of_conduct, ethics"),
    (re.compile(r"penalty|penalt|offence|offense|violation|misconduct|punish", re.I),
     "penalties, legal_framework, misconduct"),
    (re.compile(r"prescription|prescrib|medicine|drug|medication", re.I),
     "prescribing_rules, telemedicine_prescribing"),
    (re.compile(r"emergency|urgent|life.threaten|refer|escalat", re.I),
     "emergency_escalation, referral_duty"),
    (re.compile(r"platform|technology|app|software|digital|interface", re.I),
     "technology_platform, intermediary"),
    (re.compile(r"patient.*eligible|who.*can|who.*may|caregiver|guardian|carer", re.I),
     "patient_eligibility, caregiver_consent"),
    (re.compile(r"record|documentation|log|maintain|store|retain", re.I),
     "record_keeping, documentation, audit"),
    (re.compile(r"identification|verify|authenticate|identity", re.I),
     "patient_identification, verification"),
    (re.compile(r"scope|limitation|boundary|cannot|must not|shall not", re.I),
     "scope_boundary, limitations, prohibitions"),
    (re.compile(r"framework|legal|regulation|guideline|act|rule|law", re.I),
     "legal_framework, regulatory_basis"),
]

_SKIP_METADATA_SECTIONS: frozenset[str] = frozenset({
    "introduction", "background", "foreword", "preface", "preamble",
    "references", "acknowledgments", "acknowledgements",
    "conflict of interest", "disclosure", "funding",
    "abbreviations", "appendix", "abstract",
    "table of contents", "contents", "glossary",
    "summary", "conclusion", "recommendations",
    "figure", "table", "annex", "telemedicine",
    "telehealth", "registered medical practitioner",
    "purpose", "exclusions",
})

_HEADING_RE = re.compile(r"(?m)^(#{1,4})\s+(.+)$")

# ── Drug list annotation ───────────────────────────────────────────────────────
# pdfplumber extracts the drug list sections as table cells, so the drug list
# definitions appear embedded inside table rows, e.g.:
#   "|  |  |  List O: It will comprise those medicines..."
#   "|  |  |  List A: These medications are those which..."
#   "|  |  List B: Is a list of medication..."
# The regex matches "List O/A/B :" anywhere on a line (colon after the label
# marks the definitional occurrence vs. mere references like "under List A").
_DRUG_LIST_INLINE_RE = re.compile(
    r"(?m)^(.*?)\b(List\s+([OAB]))\s*:",
    re.I,
)

LIST_RULES = {
    "O": (
        "OTC_prescribing, any_consultation_mode, text_permitted, "
        "no_prior_visit_required"
    ),
    "A": (
        "restricted_prescribing, established_patient_or_audio_video, "
        "text_insufficient_for_new_patient"
    ),
    "B": (
        "video_only_prescribing, video_consultation_mandatory, "
        "text_and_audio_insufficient"
    ),
}

# ── Consultation-flow keyword detector ───────────────────────────────────────
_FLOW_KEYWORDS_RE = re.compile(
    r"\b(text.only|audio.only|video.consult|first.consult|follow.up|"
    r"new patient|established patient|asynchronous|synchronous|"
    r"real.time|phone.call|telephone.consult|video.call)\b",
    re.I,
)


# ── Section metadata injection ────────────────────────────────────────────────
def _section_tags(heading_text: str) -> str:
    for pattern, tags in SECTION_TAG_MAP:
        if pattern.search(heading_text):
            return tags
    return "general"


def _inject_section_metadata(md: str) -> str:
    def _replacer(match: re.Match) -> str:
        hashes = match.group(1)
        title = match.group(2).strip()
        if title.rstrip(".").lower() in _SKIP_METADATA_SECTIONS:
            return f"{hashes} {title}"
        tags = _section_tags(title)
        comment = (
            f"\n<!-- rag_metadata source={SOURCE_KEY} "
            f"section=\"{title}\" "
            f"topic_tags=\"{tags}\" "
            f"namespace=\"compliance\" "
            f"year={YEAR} -->"
        )
        return f"{hashes} {title}{comment}"
    return _HEADING_RE.sub(_replacer, md)


# ── Drug list annotation ──────────────────────────────────────────────────────
def _annotate_drug_lists(md: str) -> str:
    """
    Prepend a rag_metadata comment before each "List O/A/B" bullet line.
    The PDF renders list headers as inline bullets, not ATX headings, so we
    match the bullet prefix + "List X :" pattern directly.
    """
    def _replace(match: re.Match) -> str:
        label = match.group(2)        # "List A"
        letter = match.group(3).upper()
        rule_tags = LIST_RULES.get(letter, "prescribing_category")
        comment = (
            f"<!-- rag_metadata source={SOURCE_KEY} "
            f"list_type=\"{label}\" "
            f"topic_tags=\"drug_list_{letter}, {rule_tags}\" "
            f"namespace=\"compliance\" -->\n"
        )
        return comment + match.group(0)
    return _DRUG_LIST_INLINE_RE.sub(_replace, md)


# ── Consultation-flow annotation ──────────────────────────────────────────────
def _annotate_consultation_flows(md: str) -> str:
    """
    Prepend a rag_metadata comment before lines that contain consultation-mode
    decision keywords. Only fires on substantive lines (>40 chars) that are
    not already headings or comments.
    """
    out_lines: list[str] = []
    for line in md.splitlines():
        stripped = line.strip()
        if (
            _FLOW_KEYWORDS_RE.search(stripped)
            and len(stripped) > 40
            and not stripped.startswith("<!--")
            and not stripped.startswith("#")
        ):
            m = _FLOW_KEYWORDS_RE.search(stripped)
            mode_hint = m.group(0).lower().replace(" ", "_").replace("-", "_") if m else "consultation_flow"
            comment = (
                f"<!-- rag_metadata source={SOURCE_KEY} "
                f"flow_step=\"{mode_hint}\" "
                f"topic_tags=\"consultation_workflow, {mode_hint}\" "
                f"namespace=\"compliance\" -->"
            )
            out_lines.append(comment)
        out_lines.append(line)
    return "\n".join(out_lines)


# ── Block-to-Markdown converter ───────────────────────────────────────────────
def _blocks_to_markdown(doc) -> str:
    """Convert NarrativeParser blocks to structured Markdown."""
    lines: list[str] = []
    last_section = ""

    for block in doc.blocks:
        bt = block.block_type

        if bt == "heading":
            title = block.text.strip()
            # Numbered section headings (e.g. "3.7.4 Prescribing Medicines") → ##
            # ALL-CAPS short headings → ##
            if title != last_section:
                last_section = title
                lines.append(f"\n## {title}\n")

        elif bt == "narrative":
            text = block.text.strip()
            if text:
                lines.append(f"\n{text}\n")

        elif bt == "table":
            raw = getattr(block, "raw_table", None)
            if raw:
                rows = [[re.sub(r"\s+", " ", str(c or "").replace("|", "/")).strip() for c in row] for row in raw]
                rows = [r for r in rows if any(c for c in r)]
                if rows:
                    num_cols = max(len(r) for r in rows)
                    rows = [r + [""] * (num_cols - len(r)) for r in rows]
                    sep = ["---"] * num_cols
                    lines.append("| " + " | ".join(rows[0]) + " |")
                    lines.append("| " + " | ".join(sep) + " |")
                    for row in rows[1:]:
                        lines.append("| " + " | ".join(row) + " |")
                    lines.append("")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    if not PDF_PATH.exists():
        print(f"[ERROR] PDF not found: {PDF_PATH.resolve()}")
        sys.exit(1)

    sys.path.insert(0, str(ROOT))
    from ingestion.parsers.narrative import NarrativeParser

    print("Telemedicine Practice Guidelines India 2020 — pdfplumber extractor")
    print(f"  Input  : {PDF_PATH.resolve()}")
    print(f"  Output : {OUT_FILE.resolve()}")
    print(f"  Namespace: compliance")
    print()
    print("  Parsing ...", end=" ", flush=True)

    parser = NarrativeParser()
    doc = parser.parse(PDF_PATH, SOURCE_KEY)

    headings = sum(1 for b in doc.blocks if b.block_type == "heading")
    narratives = sum(1 for b in doc.blocks if b.block_type == "narrative")
    tables = sum(1 for b in doc.blocks if b.block_type == "table")
    print(f"OK  ({len(doc.blocks):,} blocks — {headings} headings, {narratives} narrative, {tables} tables)")

    md = _blocks_to_markdown(doc)
    md = _annotate_drug_lists(md)
    md = _annotate_consultation_flows(md)
    md = _inject_section_metadata(md)

    full_md = RAG_HEADER + md

    # Quality signals
    list_o = full_md.count('list_type="List O"')
    list_a = full_md.count('list_type="List A"')
    list_b = full_md.count('list_type="List B"')
    flow_tags = full_md.count("consultation_workflow")
    table_count = full_md.count("| --- |")

    print(
        f"  Annotations — List O:{list_o}  List A:{list_a}  List B:{list_b}  "
        f"flow tags:{flow_tags}  tables:{table_count}"
    )

    OUT_FILE.write_text(full_md, encoding="utf-8")
    print(f"\n  Saved : {OUT_FILE.resolve()}")
    print(f"  Total chars : {len(full_md):,}")


if __name__ == "__main__":
    main()
