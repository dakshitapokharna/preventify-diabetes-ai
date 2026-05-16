"""
ESC 2023 Guidelines on Cardiovascular Disease in Diabetes — Docling extractor.

Why Docling is required here:
  - Dense two-column layout throughout; pdfplumber fuses columns and garbles
    reading order for body text and algorithm flowcharts.
  - Recommendation tables have a fixed "Recommendations | Class | Level" schema
    (three-column with Class I/IIa/IIb/III and Level A/B/C) — Docling's table
    model reconstructs these correctly; pdfplumber collapses them to plain text.
  - Coloured diagnostic algorithm boxes (SCORE2-Diabetes, revascularisation
    decision trees) are rendered as Docling figure items; their surrounding text
    paragraphs carry the clinical content we need.

RAG-specific design choices:
  1. html.unescape() — restores < > operators in CV thresholds
     (e.g. "LDL-C < 1.4 mmol/L", "HbA1c < 7%", "eGFR < 30 mL/min/1.73 m²").
  2. Grid table rendering — cell-identity tracking prevents row-span duplication
     in the recommendation tables; footnote rows collapsed.
  3. _annotate_esc_recommendation_blocks() — detects the ESC three-column
     recommendation table pattern (Recommendations | Class | Level) and prepends
     a rag_metadata comment with evidence_class and evidence_level fields so the
     chunker can expose Class I / Level A recommendations for safety-critical
     cardio queries.
  4. _annotate_class_level_inline() — catches free-text "Class I, Level A" or
     "(Class IIa, Level B)" patterns that appear outside tables in the body text.
  5. _inject_section_metadata() — adds rag_metadata HTML comments after
     substantive headings; the SCORE2-Diabetes sections get a dedicated tag so
     queries like "how do I calculate CV risk in a diabetic patient" rank them
     first.

Trigger: fires only when the cardio flag is raised in the conversation engine.
Population: T2DM + established CVD, or T2DM with high/very-high CV risk.
india_specific: false — global ESC guideline used as specialist override; RSSDI
  2022 / ICMR STW remain primary for standard T2DM queries.

Output: parsed/ESC_2023_CVD_DM_docling.md

Usage:
    python ingestion/extractors/tier2/esc_2023.py
"""

from __future__ import annotations

import html
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent.parent.parent
PDF_PATH = ROOT / "corpus/tier2_condition/ESC_2023_CV_DM/ESC_2023_CVD_Diabetes_Guidelines.pdf"
OUT_DIR = ROOT / "parsed"
OUT_FILE = OUT_DIR / "ESC_2023_CVD_DM_docling.md"

SOURCE_KEY = "ESC_2023_CVD_DM"
CITATION = (
    "Marx N et al. 2023 ESC Guidelines on the management of cardiovascular disease "
    "in patients with diabetes. Eur Heart J. 2023;44(39):4043–4140"
)
YEAR = 2023

# ── RAG document-level header ─────────────────────────────────────────────────
RAG_HEADER = f"""\
<!-- rag_metadata
  source: {SOURCE_KEY}
  title: 2023 ESC Guidelines on the Management of Cardiovascular Disease in Patients with Diabetes
  citation: {CITATION}
  year: {YEAR}
  population: Adults with T2DM (or T1DM) plus established CVD, or at high/very-high CV risk
  topic_tags: cardiovascular, CVD_risk, ASCVD, heart_failure, coronary_artery_disease, atrial_fibrillation, stroke, PAD, SCORE2_diabetes, lipids, blood_pressure, antithrombotic, SGLT2_inhibitors, GLP1_agonists, revascularisation, T2DM, T1DM
  retrieval_tier: triggered
  condition_trigger: cardio
  india_specific: false
  age_scope: adult
  evidence_grade: ESC_Class_I_IIa_IIb_III / Level_A_B_C
-->

# ESC 2023 Guidelines — Cardiovascular Disease in Diabetes

**Source:** {SOURCE_KEY}
**Citation:** {CITATION}
**Population:** Adults with diabetes (T2DM or T1DM) who have established cardiovascular
disease or are at high / very-high CV risk. Also covers T2DM patients with heart failure,
atrial fibrillation, stroke, PAD, and CKD.
**Scope:** CV risk stratification (SCORE2-Diabetes), cardioprotective drug selection
(SGLT2i, GLP-1 RAs), LDL-C and BP targets, antithrombotic therapy, revascularisation
decisions, HF management, and lifestyle modification.

> **Retrieval note:** TRIGGERED source — queried only when the cardio flag fires.
> For standard T2DM drug / glycaemic queries, RSSDI 2022 and ICMR STW 2024 take
> priority. For cardio-specific queries in a T2DM patient, ESC 2023 overrides Tier 1
> sources.
> ESC evidence grades: Class I = recommended; IIa = should be considered; IIb = may
> be considered; III = not recommended. Level A = multiple RCTs; B = single RCT or
> large registry; C = expert consensus.

---
"""

# ── Section-level metadata map ─────────────────────────────────────────────────
SECTION_TAG_MAP: list[tuple[re.Pattern, str]] = [
    # CV risk scoring — SCORE2-Diabetes is the primary ESC 2023 innovation
    (re.compile(r"SCORE2.diabet|risk.scor|risk.stratif|CV.risk|cardiovascular.risk|risk.categor|risk.classif", re.I),
     "CVD_risk, SCORE2_diabetes, risk_stratification"),
    # Established ASCVD / atherosclerosis
    (re.compile(r"ASCVD|atherosclerosis|atherosclerotic|established.CV|prior.CV|secondary.prevention", re.I),
     "ASCVD, secondary_prevention, CVD_risk"),
    # Coronary artery disease / ACS
    (re.compile(r"coronary|CAD\b|ACS\b|STEMI|NSTEMI|angina|myocardial.infarction|MI\b|revascular", re.I),
     "coronary_artery_disease, ACS, revascularisation"),
    # Heart failure
    (re.compile(r"heart.failure|HFrEF|HFpEF|HFmrEF|cardiac.function|ejection.fraction|LVEF|NYHA", re.I),
     "heart_failure, HFrEF, HFpEF, ejection_fraction"),
    # Atrial fibrillation
    (re.compile(r"atrial.fibrillation|AF\b|AFib|anticoagul|DOAC|NOACs?|warfarin|stroke.prevention", re.I),
     "atrial_fibrillation, anticoagulation, stroke_prevention"),
    # Stroke / cerebrovascular
    (re.compile(r"stroke|TIA\b|cerebrovascular|carotid|antiplatelet|aspirin", re.I),
     "stroke, TIA, cerebrovascular, antiplatelet"),
    # Peripheral arterial disease
    (re.compile(r"peripheral.arteri|PAD\b|limb.ischaemia|ankle.brachial|ABI\b|claudication", re.I),
     "PAD, peripheral_arterial_disease, limb_ischaemia"),
    # Lipids / LDL targets
    (re.compile(r"lipid|LDL|cholesterol|statin|ezetimibe|PCSK9|triglycerid|dyslipidaemia|dyslipidemia", re.I),
     "dyslipidemia, lipids, LDL_targets, statins"),
    # Blood pressure / hypertension
    (re.compile(r"blood.pressure|BP\b|hypertension|antihypertensive|ACE.inhibitor|ARB\b|RAAS|systolic|diastolic", re.I),
     "hypertension, blood_pressure, BP_targets, antihypertensive"),
    # Cardioprotective glucose-lowering drugs (core ESC 2023 message)
    (re.compile(r"SGLT2|gliflozin|empagliflozin|dapagliflozin|canagliflozin|GLP.1|liraglutide|semaglutide|dulaglutide|glucose.lower.*cardio|cardio.*glucose", re.I),
     "SGLT2_inhibitors, GLP1_agonists, cardioprotective_drugs, drug_class"),
    # Antithrombotic / antiplatelet
    (re.compile(r"antithrombotic|antiplatelet|anticoagul|dual.antiplatelet|DAPT|P2Y12|clopidogrel|ticagrelor|rivaroxaban|apixaban", re.I),
     "antithrombotic, antiplatelet, anticoagulation"),
    # Revascularisation / PCI / CABG
    (re.compile(r"revascular|PCI\b|CABG\b|bypass|stent|percutaneous.coronary|coronary.artery.bypass", re.I),
     "revascularisation, PCI, CABG, coronary_intervention"),
    # HbA1c / glycaemic targets in CVD context
    (re.compile(r"HbA1c|glycat|glycaemi|glycemic|glucose.target|blood.glucose|fasting.glucose", re.I),
     "glycemic_targets, HbA1c, glucose_control"),
    # CKD / renal function (often co-trigger with KDIGO)
    (re.compile(r"CKD|chronic.kidney|renal|creatinine|eGFR|kidney.function|dialysis|albuminuria", re.I),
     "CKD, renal_function, eGFR"),
    # Lifestyle in CVD context
    (re.compile(r"lifestyle|diet|physical.activity|exercise|weight|obesity|smoking|alcohol", re.I),
     "lifestyle_modification, CVD_prevention, diet"),
    # Imaging / investigation
    (re.compile(r"echo|echocardiograph|imaging|angiograph|CT.scan|MRI|stress.test|electrocardiograph|ECG", re.I),
     "cardiac_imaging, investigation, diagnostics"),
    # Elderly / frail patients
    (re.compile(r"elder|geriatric|older|frail|age[d>]\s*\d", re.I),
     "elderly, geriatric, frailty"),
    # Primary prevention
    (re.compile(r"primary.prevention|asymptomatic|screening|risk.factor", re.I),
     "primary_prevention, screening, CVD_risk"),
    # Glycaemic monitoring
    (re.compile(r"CGM|continuous.glucose|SMBG|self.monitor|glucose.monitor", re.I),
     "glucose_monitoring, CGM, SMBG"),
    # Algorithm / decision pathway
    (re.compile(r"algorithm|decision|pathway|flowchart|management.approach|step", re.I),
     "clinical_algorithm, decision_pathway"),
]

# Headings too generic to warrant per-section metadata
_SKIP_METADATA_SECTIONS: frozenset[str] = frozenset({
    "recommendations", "recommendation", "references", "acknowledgments",
    "acknowledgements", "conflict of interest", "disclosure", "funding",
    "abbreviations", "appendix", "introduction", "background",
    "abstract", "foreword", "preface", "table of contents", "contents",
    "figure", "table", "glossary", "summary", "what is new",
    "key messages", "preamble",
})

_MD_TABLE_RE = re.compile(
    r"(?m)^(\|[^\n]+\|\n)(\|[-: |]+\|\n)((?:\|[^\n]*\|\n)*)",
)

_HEADING_RE = re.compile(r"(?m)^(#{1,4})\s+(.+)$")

# ESC recommendation table: header row contains both "Class" and "Level" columns.
# We detect the separator line of such tables to find them.
_ESC_REC_TABLE_CLASS_RE = re.compile(
    r"(?m)^\|[^\n]*\bClass\b[^\n]*\bLevel\b[^\n]*\|$",
    re.I,
)

# Inline Class/Level patterns in body text: "Class I, Level A" or "(Class IIa, Level B)"
_INLINE_CLASS_RE = re.compile(
    r"\b(Class\s+(?:I{1,3}|IIa|IIb|III|IV))\s*[,/]\s*(Level\s+[ABC])\b",
    re.I,
)


def _clean_cell(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"\n+", " · ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("|", "/")
    return text


def _render_table_grid(table_item) -> str:
    """
    Render a Docling TableItem from its raw grid.
    - Spanning cells emitted only at top-left position (id() tracking).
    - Footnote rows (all non-empty cells identical) collapsed to first column.
    Falls back to export_to_markdown() on error.
    """
    try:
        data = table_item.data
        grid = data.grid
        num_cols = data.num_cols
    except Exception:
        return table_item.export_to_markdown()

    if not grid or num_cols == 0:
        return table_item.export_to_markdown()

    seen: set[int] = set()
    str_rows: list[list[str]] = []

    for grid_row in grid:
        row_cells: list[str] = []
        for cell in grid_row:
            if cell is None:
                row_cells.append("")
            elif id(cell) in seen:
                row_cells.append("")
            else:
                seen.add(id(cell))
                row_cells.append(_clean_cell(cell.text))
        while len(row_cells) < num_cols:
            row_cells.append("")
        str_rows.append(row_cells[:num_cols])

    if not str_rows:
        return table_item.export_to_markdown()

    # Collapse footnote rows: all non-empty cells carry the same text
    clean_rows: list[list[str]] = []
    for row in str_rows:
        non_empty = [c for c in row if c]
        if len(non_empty) > 1 and len(set(non_empty)) == 1:
            clean_rows.append([non_empty[0]] + [""] * (num_cols - 1))
        else:
            clean_rows.append(row)

    sep = ["---"] * num_cols
    lines: list[str] = [
        "| " + " | ".join(clean_rows[0]) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for row in clean_rows[1:]:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


def _section_tags(heading_text: str) -> str:
    for pattern, tags in SECTION_TAG_MAP:
        if pattern.search(heading_text):
            return tags
    return "cardiovascular, general"


def _inject_section_metadata(md: str) -> str:
    """Insert rag_metadata HTML comments after substantive ATX headings."""
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
            f"population=\"T2DM CVD\" "
            f"year={YEAR} -->"
        )
        return f"{hashes} {title}{comment}"

    return _HEADING_RE.sub(_replacer, md)


def _annotate_esc_recommendation_blocks(md: str) -> str:
    """
    Detect ESC recommendation tables (header contains both 'Class' and 'Level'
    columns) and prepend a rag_metadata comment immediately before the table.

    This ensures every recommendation block is retrievable by evidence class/level
    without relying on surrounding section context.
    """
    lines = md.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        # Look-ahead: does this line look like a table header with Class + Level?
        stripped = lines[i].strip()
        if (
            stripped.startswith("|")
            and "class" in stripped.lower()
            and "level" in stripped.lower()
            and stripped.endswith("|")
        ):
            comment = (
                f"<!-- rag_metadata source={SOURCE_KEY} "
                f"topic_tags=\"recommendation, ESC_Class_Level\" "
                f"evidence_schema=\"ESC_Class_I_IIa_IIb_III / Level_A_B_C\" "
                f"population=\"T2DM CVD\" -->\n"
            )
            out.append(comment)
        out.append(lines[i])
        i += 1
    return "".join(out)


def _annotate_class_level_inline(md: str) -> str:
    """
    Prepend a rag_metadata comment before lines containing an inline
    'Class X, Level Y' pattern (these appear in body paragraphs as well as
    inside table cells — target only body lines with substantive content).

    Only fires on lines > 60 chars to avoid annotating table separator rows or
    header-only lines.
    """
    out_lines: list[str] = []
    for line in md.splitlines():
        m = _INLINE_CLASS_RE.search(line)
        if m and len(line.strip()) > 60 and not line.strip().startswith("|"):
            cls = m.group(1).strip()
            lvl = m.group(2).strip()
            comment = (
                f"<!-- rag_metadata source={SOURCE_KEY} "
                f"evidence_class=\"{cls}\" "
                f"evidence_level=\"{lvl}\" "
                f"topic_tags=\"recommendation, {cls.replace(' ', '_')}, {lvl.replace(' ', '_')}\" -->"
            )
            out_lines.append(comment)
        out_lines.append(line)
    return "\n".join(out_lines)


def convert_document(pdf_path: Path) -> str:
    """Convert the ESC 2023 CVD-DM PDF to clean RAG-ready Markdown."""
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    doc = result.document

    md = html.unescape(doc.export_to_markdown())

    # Replace raw markdown tables with grid-rendered versions
    tables = list(doc.tables)
    counter: list[int] = [0]

    def _replace(match: re.Match) -> str:
        idx = counter[0]
        counter[0] += 1
        if idx < len(tables):
            return _render_table_grid(tables[idx]) + "\n\n"
        return match.group(0)

    md = _MD_TABLE_RE.sub(_replace, md)

    found = counter[0]
    if found != len(tables):
        print(
            f"\n  [WARN] {len(tables)} tables in doc but regex matched {found}",
            end="",
        )

    # ESC-specific annotations (order matters: tables first, then inline, then sections)
    md = _annotate_esc_recommendation_blocks(md)
    md = _annotate_class_level_inline(md)
    md = _inject_section_metadata(md)

    return md


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    if not PDF_PATH.exists():
        print(f"[ERROR] PDF not found: {PDF_PATH.resolve()}")
        sys.exit(1)

    print("ESC 2023 CVD in Diabetes — Docling extractor")
    print(f"  Input  : {PDF_PATH.resolve()}")
    print(f"  Output : {OUT_FILE.resolve()}")
    print()
    print("  Converting ... (ESC guidelines are large; expect 5–15 min on CPU)", flush=True)

    try:
        md = convert_document(PDF_PATH)
    except Exception as exc:
        print(f"ERROR — {exc}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    # Quality signals
    class_i_hits = len(re.findall(r"\bClass\s+I\b", md, re.I))
    level_a_hits = len(re.findall(r"\bLevel\s+A\b", md, re.I))
    score2_hits = md.lower().count("score2")
    table_count = md.count("| --- |")
    print(
        f"OK  ({len(md):,} chars, ~{table_count} tables, "
        f"~{class_i_hits} Class I refs, ~{level_a_hits} Level A refs, "
        f"~{score2_hits} SCORE2-Diabetes refs)"
    )

    full_md = RAG_HEADER + md

    OUT_FILE.write_text(full_md, encoding="utf-8")
    print(f"\n  Saved : {OUT_FILE.resolve()}")
    print(f"  Total chars : {len(full_md):,}")
    print(f"  Recommendation table blocks annotated: {full_md.count('evidence_schema=\"ESC_Class')}")
    print(f"  Inline Class/Level annotations       : {full_md.count('evidence_class=')}")


if __name__ == "__main__":
    main()
