"""
RSSDI Clinical Practice Recommendations for T2DM 2022 — extractor.

Uses the existing RSSDirectParser (pdfplumber) which handles RSSDI's landscape
single-column layout, ALL-CAPS section headings, bullet recommendations, and
inline evidence grades (A)/(B)/(C)/(E) correctly.

Docling is not used here because the 236-page PDF runs out of RAM during the
VLM table-structure preprocessing stage (std::bad_alloc) even with OCR disabled.

This script:
  1. Runs RSSDirectParser to get typed blocks (heading, recommendation,
     narrative, table).
  2. Converts blocks to structured Markdown with heading hierarchy.
  3. Applies _inject_section_metadata() — adds rag_metadata HTML comments
     after substantive headings so the chunker can tag each chunk.
  4. Applies _annotate_evidence_grades() — prepends rag_metadata comments
     before lines with (A)/(B)/(C)/(E) grade markers so grade-filtered
     retrieval can surface Grade A recommendations preferentially.
  5. Adds a RAG document header.

Output: parsed/RSSDI_2022_docling.md

Usage:
    python extract_rssdi_docling.py
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent.parent.parent
PDF_PATH = ROOT / "corpus/tier1_clinical/RSSDI_2022/RSSDI_Clinical_Practice_Recommendations_T2DM_2022.pdf"
OUT_DIR = ROOT / "parsed"
OUT_FILE = OUT_DIR / "RSSDI_2022_docling.md"

SOURCE_KEY = "RSSDI_2022"
CITATION = (
    "RSSDI Clinical Practice Recommendations for the Management of Type 2 Diabetes Mellitus 2022. "
    "Research Society for the Study of Diabetes in India. Int J Diabetes Dev Ctries. 2022;42(Suppl 1):1–143"
)
YEAR = 2022

# ── RAG document-level header ─────────────────────────────────────────────────
RAG_HEADER = f"""\
<!-- rag_metadata
  source: {SOURCE_KEY}
  title: RSSDI Clinical Practice Recommendations for the Management of Type 2 Diabetes Mellitus 2022
  citation: {CITATION}
  year: {YEAR}
  population: Adult T2DM patients, India
  topic_tags: T2DM, clinical_practice_recommendations, glycemic_targets, drug_selection, lifestyle, complication_screening, india_specific, evidence_graded
  retrieval_tier: core
  condition_trigger: null
  india_specific: true
  age_scope: adult
  evidence_grade: A/B/C/E
-->

# RSSDI Clinical Practice Recommendations for T2DM 2022

**Source:** {SOURCE_KEY}
**Citation:** {CITATION}
**Population:** Adult T2DM patients in India
**Scope:** Comprehensive clinical practice recommendations covering glycemic targets,
drug selection and escalation, lifestyle modification, complication screening,
management of T2DM with comorbidities (hypertension, dyslipidemia, CKD, CVD),
and special populations (elderly, pregnancy, fasting).

> **Clinical priority note:** PRIMARY source for all standard T2DM queries.
> Prefer RSSDI 2022 over ADA 2026 for drug selection, glycemic targets, and
> complication screening in the Indian population.
> Evidence grades: (A) = highest, (B) = moderate, (C) = limited, (E) = expert consensus.

---
"""

# ── Section-level metadata map ─────────────────────────────────────────────────
SECTION_TAG_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"glycem|HbA1c|A1c|target|control|fasting|postprandial", re.I),
     "glycemic_targets, HbA1c"),
    (re.compile(r"metformin|first.line|initial|monotherapy", re.I),
     "first_line_therapy, metformin"),
    (re.compile(r"sulfonyl|SU\b|glimep|glibenc|gliclaz", re.I),
     "sulfonylureas, drug_class"),
    (re.compile(r"DPP.4|gliptin|sitagliptin|vildagliptin|teneligliptin", re.I),
     "DPP4_inhibitors, drug_class"),
    (re.compile(r"SGLT2|gliflozin|dapagliflozin|empagliflozin|canagliflozin", re.I),
     "SGLT2_inhibitors, drug_class"),
    (re.compile(r"GLP.1|liraglutide|semaglutide|exenatide|dulaglutide", re.I),
     "GLP1_agonists, drug_class"),
    (re.compile(r"insulin|basal|premix|bolus|NPH|degludec|glargine|aspart", re.I),
     "insulin, injectable_therapy"),
    (re.compile(r"thiazolidinedione|pioglitazone|TZD", re.I),
     "thiazolidinediones, drug_class"),
    (re.compile(r"lifestyle|diet|nutrition|physical.activity|exercise|weight|obesity", re.I),
     "lifestyle_modification, nutrition, physical_activity"),
    (re.compile(r"screen|complication|nephropathy|retinopathy|neuropathy|foot|fundus", re.I),
     "complication_screening, microvascular"),
    (re.compile(r"CKD|kidney|renal|creatinine|eGFR|dialysis", re.I),
     "CKD, renal_dosing, eGFR"),
    (re.compile(r"cardio|cardiovascular|CVD|heart|coronary|ASCVD|atherosclerosis", re.I),
     "cardiovascular, CVD_risk"),
    (re.compile(r"BP|blood.pressure|hypertension|antihypertensive", re.I),
     "hypertension, blood_pressure"),
    (re.compile(r"lipid|dyslipidemia|cholesterol|statin|triglyceride|LDL|HDL", re.I),
     "dyslipidemia, lipids, statins"),
    (re.compile(r"hypoglycemia|low.sugar|low.glucose|hypoglycaemia", re.I),
     "hypoglycemia, safety"),
    (re.compile(r"elder|geriatric|older|frail|age[d>]", re.I),
     "elderly, geriatric"),
    (re.compile(r"pregnan|gestational|GDM|antenatal|lactation", re.I),
     "pregnancy, GDM"),
    (re.compile(r"fast|ramadan|roza|religious", re.I),
     "fasting, ramadan, religious"),
    (re.compile(r"diagnos|criteria|classify|classification|detection", re.I),
     "diagnosis, classification"),
    (re.compile(r"self.monitor|SMBG|CGM|glucose.monitor|blood.glucose.monitor", re.I),
     "glucose_monitoring, SMBG"),
    (re.compile(r"refer|escalat|specialist|hospital", re.I),
     "referral, escalation"),
    (re.compile(r"bariatric|surgery|metabolic.surgery", re.I),
     "bariatric_surgery, obesity"),
    (re.compile(r"algorithm|escalat|intensif|add.on|combination", re.I),
     "treatment_algorithm, drug_escalation"),
    (re.compile(r"south.asian|indian|asian|ethnicity|body.composition", re.I),
     "south_asian, india_specific, body_composition"),
]

_SKIP_METADATA_SECTIONS: frozenset[str] = frozenset({
    "recommendations", "recommendation", "references", "acknowledgments",
    "acknowledgements", "conflict of interest", "disclosure", "funding",
    "abbreviations", "appendix", "introduction", "background",
    "abstract", "foreword", "preface", "table of contents", "contents",
    "figure", "table", "glossary", "summary",
})

_HEADING_RE = re.compile(r"(?m)^(#{1,4})\s+(.+)$")

_GRADE_RE = re.compile(r"\(([ABCE])\)|\bGrade\s+([ABCE])\b", re.I)


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
            f"population=\"T2DM India\" "
            f"year={YEAR} -->"
        )
        return f"{hashes} {title}{comment}"
    return _HEADING_RE.sub(_replacer, md)


def _annotate_evidence_grades(md: str) -> str:
    """Prepend a rag_metadata comment before lines with RSSDI grade markers.

    Collects ALL grade letters found on the line (a single recommendation can
    reference multiple grades, e.g. '...ACEi (A) or ARB if intolerant (B)').
    The highest grade found is used as the primary evidence_grade field so
    grade-filtered retrieval always surfaces the strongest evidence first.
    """
    _GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "E": 3}

    out_lines: list[str] = []
    for line in md.splitlines():
        if len(line.strip()) > 40:
            grades = [
                (m.group(1) or m.group(2)).upper()
                for m in _GRADE_RE.finditer(line)
                if (m.group(1) or m.group(2))
            ]
            if grades:
                primary = min(grades, key=lambda g: _GRADE_ORDER.get(g, 99))
                all_grades = ",".join(sorted(set(grades), key=lambda g: _GRADE_ORDER.get(g, 99)))
                comment = (
                    f"<!-- rag_metadata source={SOURCE_KEY} "
                    f"evidence_grade=\"{primary}\" "
                    f"all_grades=\"{all_grades}\" "
                    f"topic_tags=\"recommendation, grade_{primary}\" -->"
                )
                out_lines.append(comment)
        out_lines.append(line)
    return "\n".join(out_lines)


def _blocks_to_markdown(doc) -> str:
    """Convert RSSDirectParser blocks to structured Markdown."""
    lines: list[str] = []
    last_section = ""

    for block in doc.blocks:
        bt = block.block_type

        if bt == "heading":
            title = block.text.strip().title()  # Convert ALL-CAPS to Title Case
            if title != last_section:
                last_section = title
                lines.append(f"\n## {title}\n")

        elif bt == "recommendation":
            grade = block.evidence_grade or ""
            grade_suffix = f" ({grade})" if grade else ""
            lines.append(f"- {block.text}{grade_suffix}")

        elif bt == "narrative":
            lines.append(f"\n{block.text}\n")

        elif bt == "table":
            raw = getattr(block, "raw_table", None)
            if raw:
                # Convert raw table (list of lists) to markdown table
                rows = [[re.sub(r"\s+", " ", str(c or "").replace("|", "/")).strip() for c in row] for row in raw]
                # Filter empty rows
                rows = [r for r in rows if any(c for c in r)]
                if rows:
                    num_cols = max(len(r) for r in rows)
                    # Pad rows to same width
                    rows = [r + [""] * (num_cols - len(r)) for r in rows]
                    sep = ["---"] * num_cols
                    lines.append("| " + " | ".join(rows[0]) + " |")
                    lines.append("| " + " | ".join(sep) + " |")
                    for row in rows[1:]:
                        lines.append("| " + " | ".join(row) + " |")
                    lines.append("")

    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    if not PDF_PATH.exists():
        print(f"[ERROR] PDF not found: {PDF_PATH.resolve()}")
        sys.exit(1)

    sys.path.insert(0, str(ROOT))
    from ingestion.parsers.recommendation import RSSDirectParser

    print("RSSDI 2022 — pdfplumber extractor")
    print(f"  Input  : {PDF_PATH.resolve()}")
    print(f"  Output : {OUT_FILE.resolve()}")
    print()
    print("  Parsing ...", end=" ", flush=True)

    parser = RSSDirectParser()
    doc = parser.parse(PDF_PATH, SOURCE_KEY)

    headings = sum(1 for b in doc.blocks if b.block_type == "heading")
    recs = sum(1 for b in doc.blocks if b.block_type == "recommendation")
    grade_a = sum(1 for b in doc.blocks if b.block_type == "recommendation" and b.evidence_grade == "A")
    tables = sum(1 for b in doc.blocks if b.block_type == "table")
    print(f"OK  ({len(doc.blocks):,} blocks — {headings} sections, {recs} recommendations, {grade_a} Grade A, {tables} tables)")

    md = _blocks_to_markdown(doc)
    md = _inject_section_metadata(md)
    md = _annotate_evidence_grades(md)

    full_md = RAG_HEADER + md

    OUT_FILE.write_text(full_md, encoding="utf-8")
    print(f"\n  Saved : {OUT_FILE.resolve()}")
    print(f"  Total chars : {len(full_md):,}")
    print(f"  Tables      : {full_md.count('| --- |')}")


if __name__ == "__main__":
    main()
