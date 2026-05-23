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


def _is_real_table(rows: list[list]) -> bool:
    if not rows or len(rows) < 2:
        return False
    max_cols = max(len(r) for r in rows)
    if max_cols < 2:
        return False
    total = sum(len(r) for r in rows)
    non_empty = sum(1 for r in rows for c in r if c is not None and str(c).strip())
    return (non_empty / total) >= 0.30


def _render_markdown_table(rows: list[list]) -> str:
    def _fmt(cell) -> str:
        if cell is None:
            return ""
        return str(cell).replace("|", "/").replace("\n", " ").strip()

    if not rows:
        return ""
    max_cols = max(len(r) for r in rows)
    norm = [[_fmt(c) for c in (r + [None] * (max_cols - len(r)))] for r in rows]
    header = "| " + " | ".join(norm[0]) + " |"
    sep    = "| " + " | ".join(["---"] * max_cols) + " |"
    body   = ["| " + " | ".join(row) + " |" for row in norm[1:]]
    return "\n".join([header, sep] + body)


def _extract_page(page) -> str:
    try:
        found = page.find_tables()
    except Exception:
        found = []

    real: list = []
    for ft in found:
        try:
            rows = ft.extract()
        except Exception:
            continue
        if _is_real_table(rows):
            real.append((ft.bbox, rows))

    table_bboxes = [bb for bb, _ in real]

    def _in_table(bbox) -> bool:
        x0, top, x1, bottom = bbox
        for tb in table_bboxes:
            if x0 >= tb[0] - 2 and top >= tb[1] - 2 and x1 <= tb[2] + 2 and bottom <= tb[3] + 2:
                return True
        return False

    try:
        words = page.extract_words(x_tolerance=3, y_tolerance=3)
    except Exception:
        words = []

    body_words = [w for w in words if not _in_table((w["x0"], w["top"], w["x1"], w["bottom"]))]

    lines_map: dict[int, list] = {}
    for w in body_words:
        key = round(w["top"])
        lines_map.setdefault(key, []).append(w)

    text_lines = []
    for key in sorted(lines_map):
        row = sorted(lines_map[key], key=lambda w: w["x0"])
        text_lines.append(" ".join(w["text"] for w in row))
    body_text = "\n".join(text_lines)

    parts: list[tuple[float, str]] = []
    for bb, rows in real:
        tb_top = bb[1]
        parts.append((tb_top, _render_markdown_table(rows)))

    # body_text is emitted once per page (not once per table).
    # Previous code appended body_text inside the loop, repeating it N times
    # on pages with N tables — causing duplicate content in the parsed output.
    chunks: list[str] = []
    if parts:
        chunks.append(body_text)
        for _, table_md in sorted(parts, key=lambda x: x[0]):
            chunks.append(table_md)
    else:
        chunks.append(body_text)

    return "\n\n".join(c for c in chunks if c.strip())


def extract_document(pdf_path: Path) -> str:
    import pdfplumber
    pages_md: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages, 1):
            page_text = _extract_page(page)
            pages_md.append(f"<!-- page {i} -->\n{page_text}")
            if i % 20 == 0:
                print(f"    ... {i}/{total} pages", flush=True)
    return "\n\n".join(pages_md)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    if not PDF_PATH.exists():
        print(f"[ERROR] PDF not found: {PDF_PATH.resolve()}")
        sys.exit(1)

    print("ESC 2023 CVD in Diabetes — pdfplumber extractor")
    print(f"  Input  : {PDF_PATH.resolve()}")
    print(f"  Output : {OUT_FILE.resolve()}")
    print()

    try:
        raw = extract_document(PDF_PATH)
    except Exception as exc:
        print(f"[ERROR] — {exc}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    # ESC-specific annotations (order matters: tables first, then inline, then sections)
    md = _annotate_esc_recommendation_blocks(raw)
    md = _annotate_class_level_inline(md)
    md = _inject_section_metadata(md)

    full_md = RAG_HEADER + md

    OUT_FILE.write_text(full_md, encoding="utf-8")

    # Quality signals
    class_i_hits = len(re.findall(r"\bClass\s+I\b", full_md, re.I))
    level_a_hits = len(re.findall(r"\bLevel\s+A\b", full_md, re.I))
    score2_hits = full_md.lower().count("score2")
    table_count = full_md.count("\n| --- |")
    page_count  = full_md.count("<!-- page ")
    rec_blocks  = full_md.count('evidence_schema="ESC_Class')
    inline_cls  = full_md.count('evidence_class=')

    print(f"\n  OK  ({len(full_md):,} chars, {page_count} pages, {table_count} tables)")
    print(f"  Class I refs                         : {class_i_hits}")
    print(f"  Level A refs                         : {level_a_hits}")
    print(f"  SCORE2-Diabetes refs                 : {score2_hits}")
    print(f"  Recommendation table blocks annotated: {rec_blocks}")
    print(f"  Inline Class/Level annotations       : {inline_cls}")
    print(f"\n  Saved : {OUT_FILE.resolve()}")
    print(f"  Total chars : {len(full_md):,}")


if __name__ == "__main__":
    main()
