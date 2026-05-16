"""
WHO HEARTS Technical Package — Docling extractor.

Why Docling:
  WHO HEARTS is a modular technical package with structured single-column layouts,
  flowchart-style clinical algorithms, step-up treatment protocol tables, and CVD
  risk-scoring charts. Docling's layout analysis handles these correctly without
  column-fusion or reading-order errors. No std::bad_alloc issues expected on this
  document (unlike ICMR-NIN, IDF-DAR, KDIGO which are 100+ dense pages with
  VLM-crashing table density).

WHO HEARTS module structure (HEARTS acronym):
  H — Healthy-lifestyle counselling
  E — Evidence-based treatment protocols (step-up antihypertensive ladders)
  A — Access to essential medicines and technology
  R — Risk-based CVD management (WHO/ISH 10-year CVD risk charts)
  T — Team-based care and task sharing
  S — Systems for monitoring

RAG-specific design choices:
  1. html.unescape() — restores ≥ / ≤ / < / > operators in BP thresholds
     (e.g. "systolic BP ≥ 140 mmHg", "target < 130/80 mmHg").
  2. Grid table rendering — cell-identity tracking prevents row-span duplication
     in protocol tables; footnote rows collapsed.
  3. _annotate_treatment_protocol_steps() — detects numbered step lines in the
     antihypertensive titration ladders ("Step 1 / Step 2 / Step 3") and prepends
     a rag_metadata comment with chunk_note: keep_atomic_large_window so the
     chunker never splits a protocol ladder mid-sequence.
  4. _annotate_bp_decision_thresholds() — detects lines carrying specific BP
     threshold values used as clinical decision points (≥140/90, ≥130/80,
     target <130/80, systolic <120 mmHg) and prepends a rag_metadata comment
     with safety_critical=true; these are the initiate/intensify triggers.
  5. _annotate_cvd_risk_scoring() — detects lines referencing 10-year CVD risk
     scores, risk categories (low/moderate/high/very high), or WHO/ISH risk chart
     references and prepends chunk_note: keep_atomic_large_window so risk-chart
     rows are never separated from their threshold context.
  6. _inject_section_metadata() — adds rag_metadata HTML comments after
     substantive headings; HEARTS module headings get their module tag so every
     sub-chunk inherits module context without relying on surrounding text.

No content filtering or format assumptions. All annotation passes are purely
additive (insert comment lines before matching lines; nothing is dropped or
reformatted beyond html.unescape and grid table rendering).

Trigger: fires only when the hypertension flag is raised in the conversation engine.
Population: Adults with hypertension, or T2DM patients with elevated BP.
india_specific: false — WHO global technical package used as protocol scaffold.

Output: parsed/WHO_HEARTS_docling.md

Usage:
    python ingestion/extractors/tier2/who_hearts.py
"""

from __future__ import annotations

import html
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent.parent.parent
PDF_PATH = ROOT / "corpus/tier2_condition/WHO_HEARTS/WHO_HEARTS_Technical_Package.pdf"
OUT_DIR = ROOT / "parsed"
OUT_FILE = OUT_DIR / "WHO_HEARTS_docling.md"

SOURCE_KEY = "WHO_HEARTS"
CITATION = (
    "World Health Organization. HEARTS Technical Package for Cardiovascular Disease "
    "Management in Primary Health Care. Geneva: WHO, 2018 (updated 2020)"
)
YEAR = 2020

# ── RAG document-level header ─────────────────────────────────────────────────
RAG_HEADER = f"""\
<!-- rag_metadata
  source: {SOURCE_KEY}
  title: WHO HEARTS Technical Package for Cardiovascular Disease Management in Primary Health Care
  citation: {CITATION}
  year: {YEAR}
  population: Adults with hypertension or elevated cardiovascular risk in primary health care settings
  topic_tags: hypertension, blood_pressure, CVD_risk, antihypertensive, treatment_protocol, step_up_therapy, lifestyle_counselling, healthy_lifestyle, essential_medicines, risk_stratification, CVD_risk_charts, WHO_ISH, team_based_care, task_sharing, monitoring, quality_indicators, primary_health_care, T2DM_comorbidity
  retrieval_tier: triggered
  condition_trigger: hypertension
  india_specific: false
  age_scope: adult
  evidence_grade: WHO_consensus
  hearts_modules: H_lifestyle, E_treatment_protocols, A_access_medicines, R_risk_management, T_team_care, S_monitoring
-->

# WHO HEARTS Technical Package — CVD Management in Primary Health Care

**Source:** {SOURCE_KEY}
**Citation:** {CITATION}
**Population:** Adults with hypertension or elevated CVD risk managed in primary
health care settings. Applicable to T2DM patients with hypertension co-morbidity.
**Scope:** Six integrated modules — healthy-lifestyle counselling (H), evidence-based
antihypertensive step-up treatment protocols (E), access to essential medicines and
BP monitoring technology (A), risk-based CVD management using WHO/ISH 10-year risk
charts (R), team-based care and task sharing (T), and systems for monitoring
coverage and outcomes (S).

> **Retrieval note:** TRIGGERED source — queried only when the hypertension flag
> fires, typically alongside ESC 2023 for BP-primary queries in a T2DM patient.
> RSSDI 2022 and ICMR STW 2024 remain primary for standard T2DM glycaemic queries.
> WHO HEARTS provides the step-up antihypertensive protocol ladder and the CVD risk
> stratification framework used at primary-care level.
> Key BP thresholds (verbatim): initiate treatment if systolic BP ≥ 140 mmHg or
> diastolic BP ≥ 90 mmHg on two separate readings; treatment target < 130/80 mmHg
> for most adults with T2DM and hypertension (use clinical judgement in elderly).
> HEARTS step-up ladder (Module E): Step 1 — low-dose single agent (ACEi/ARB or
> CCB); Step 2 — full-dose ACEi/ARB + CCB; Step 3 — add thiazide/thiazide-like
> diuretic; Step 4 — specialist referral for resistant hypertension.

---

"""

# ── Section-level metadata map (HEARTS module-aware) ──────────────────────────
SECTION_TAG_MAP: list[tuple[re.Pattern, str]] = [
    # Module H — Healthy-lifestyle counselling
    (re.compile(r"healthy.lifestyle|lifestyle.counsel|HEARTS.H\b|module.H\b|module.*healthy|healthy.*module", re.I),
     "hearts_module_H, healthy_lifestyle_counselling"),
    (re.compile(r"salt|sodium|dietary.sodium|reduce.salt|low.salt|salt.intake|sodium.intake", re.I),
     "hearts_module_H, healthy_lifestyle_counselling, salt_reduction"),
    (re.compile(r"physical.activ|exercise|sedentary|aerobic|walking|MVPA|moderate.vigorous", re.I),
     "hearts_module_H, healthy_lifestyle_counselling, physical_activity"),
    (re.compile(r"alcohol|drinking|drink.reduction|alcohol.consumption", re.I),
     "hearts_module_H, healthy_lifestyle_counselling, alcohol"),
    (re.compile(r"smok|tobacco|cessation|nicotine|quit.smok", re.I),
     "hearts_module_H, healthy_lifestyle_counselling, smoking_cessation"),
    (re.compile(r"weight|obesity|overweight|BMI|body.mass|waist.circumference|weight.loss", re.I),
     "hearts_module_H, healthy_lifestyle_counselling, weight_management"),
    (re.compile(r"diet|fruit|vegetable|fibre|fiber|DASH|potassium|nutrition", re.I),
     "hearts_module_H, healthy_lifestyle_counselling, diet"),

    # Module E — Evidence-based treatment protocols (antihypertensive step-up)
    (re.compile(r"evidence.based.treat|treatment.protocol|HEARTS.E\b|module.E\b|module.*evidence|evidence.*module", re.I),
     "hearts_module_E, treatment_protocols"),
    (re.compile(r"step.up|titrat|escalat|intensif|dose.escalat|antihypertensive.protocol|protocol.ladder", re.I),
     "hearts_module_E, treatment_protocols, step_up_therapy"),
    (re.compile(r"ACE.inhibitor|ACEi|ARB\b|angiotensin|lisinopril|enalapril|ramipril|losartan|telmisartan|valsartan|perindopril|amlodipine|CCB|calcium.channel", re.I),
     "hearts_module_E, treatment_protocols, antihypertensive, drug_class"),
    (re.compile(r"thiazide|hydrochlorothiazide|chlorthalidone|indapamide|diuretic", re.I),
     "hearts_module_E, treatment_protocols, antihypertensive, diuretic, drug_class"),
    (re.compile(r"beta.blocker|atenolol|metoprolol|bisoprolol|carvedilol|beta.adrenerg", re.I),
     "hearts_module_E, treatment_protocols, antihypertensive, beta_blocker, drug_class"),
    (re.compile(r"first.line|second.line|third.line|initial.therapy|add.on|combination.therapy|single.pill", re.I),
     "hearts_module_E, treatment_protocols, combination_therapy"),
    (re.compile(r"resistant.hypertension|uncontrolled|refractory|specialist.refer", re.I),
     "hearts_module_E, treatment_protocols, resistant_hypertension, referral"),
    (re.compile(r"blood.pressure.target|BP.target|systolic.target|diastolic.target|treat.to.target", re.I),
     "hearts_module_E, treatment_protocols, BP_targets"),

    # Module A — Access to essential medicines and technology
    (re.compile(r"essential.medicine|formulary|supply.chain|medicine.availability|HEARTS.A\b|module.A\b|module.*access|access.*module", re.I),
     "hearts_module_A, essential_medicines, access"),
    (re.compile(r"BP.monitor|sphygmomanometer|blood.pressure.device|validated.device|cuff", re.I),
     "hearts_module_A, essential_medicines, BP_monitoring_device"),
    (re.compile(r"statin|aspirin|low.dose.aspirin|antiplatelet.access|cholesterol.medication", re.I),
     "hearts_module_A, essential_medicines, statin, aspirin"),
    (re.compile(r"affordab|cost|generic|procure|stockout|availability|supply.interrupt", re.I),
     "hearts_module_A, essential_medicines, access, affordability"),

    # Module R — Risk-based CVD management
    (re.compile(r"CVD.risk|cardiovascular.risk|risk.chart|WHO.ISH|risk.stratif|risk.classif|HEARTS.R\b|module.R\b|module.*risk|risk.*module", re.I),
     "hearts_module_R, CVD_risk, risk_stratification"),
    (re.compile(r"10.year.risk|ten.year.risk|absolute.risk|risk.score|risk.predict|Framingham|pooled.cohort", re.I),
     "hearts_module_R, CVD_risk, 10_year_risk, risk_scoring"),
    (re.compile(r"very.high.risk|high.risk|moderate.risk|low.risk|risk.categor|risk.level", re.I),
     "hearts_module_R, CVD_risk, risk_stratification, risk_category"),
    (re.compile(r"secondary.prevention|established.CVD|prior.MI|prior.stroke|prior.CVD|ASCVD|heart.attack", re.I),
     "hearts_module_R, CVD_risk, secondary_prevention, prior_MI"),
    (re.compile(r"primary.prevention|asymptomatic.high.risk|screening.for.CVD", re.I),
     "hearts_module_R, CVD_risk, primary_prevention"),
    (re.compile(r"lipid|cholesterol|LDL|statin.indication|dyslipidaemia|dyslipidemia", re.I),
     "hearts_module_R, CVD_risk, lipids, dyslipidemia"),

    # Module T — Team-based care and task sharing
    (re.compile(r"team.based|task.shar|task.shift|community.health.worker|CHW\b|nurse.led|protocol.driven|HEARTS.T\b|module.T\b|module.*team|team.*module", re.I),
     "hearts_module_T, team_based_care, task_sharing"),
    (re.compile(r"health.worker|non.physician|mid.level|auxiliary|lay.worker|pharmacist.role|patient.adherence", re.I),
     "hearts_module_T, team_based_care, task_sharing, health_worker"),
    (re.compile(r"patient.education|self.management|adherence|patient.engag|treatment.support", re.I),
     "hearts_module_T, team_based_care, patient_education, adherence"),

    # Module S — Systems for monitoring
    (re.compile(r"monitor|surveillance|quality.indicator|coverage|outcome.track|HEARTS.S\b|module.S\b|module.*system|system.*monitor", re.I),
     "hearts_module_S, monitoring, quality_indicators"),
    (re.compile(r"indicator|metric|KPI|data.collect|register|cohort.report|dashboard|audit", re.I),
     "hearts_module_S, monitoring, quality_indicators, data_collection"),
    (re.compile(r"BP.control.rate|hypertension.control|treatment.coverage|cascade.of.care|aware.treat.control", re.I),
     "hearts_module_S, monitoring, BP_control_rate, cascade_of_care"),

    # Cross-module: BP measurement and diagnosis
    (re.compile(r"measure.blood.pressure|take.BP|BP.measurement|office.BP|home.BP|ambulatory|ABPM|auscultatory|oscillometric", re.I),
     "BP_measurement, hypertension_diagnosis"),
    (re.compile(r"diagnos.*hypertension|hypertension.*diagnos|confirm.*hypertension|hypertension.*confirm|white.coat|masked.hypertension", re.I),
     "hypertension_diagnosis, BP_measurement"),

    # Cross-module: Special populations
    (re.compile(r"pregnan|gestational.hypertension|pre.eclampsia|eclampsia|antenatal", re.I),
     "pregnancy, gestational_hypertension, special_population"),
    (re.compile(r"elder|older|geriatric|frail|age[d>]\s*\d|octogenarian", re.I),
     "elderly, geriatric, special_population"),
    (re.compile(r"chronic.kidney|CKD|renal|creatinine|eGFR|proteinuria|microalbumin", re.I),
     "CKD, renal_function, special_population"),
    (re.compile(r"diabetes|T2DM|T1DM|diabetic", re.I),
     "diabetes, T2DM_comorbidity"),
    (re.compile(r"stroke|cerebrovascular|TIA\b|post.stroke", re.I),
     "stroke, cerebrovascular, special_population"),
    (re.compile(r"heart.failure|HFrEF|HFpEF|cardiac.failure|reduced.ejection", re.I),
     "heart_failure, special_population"),
]

_SKIP_METADATA_SECTIONS: frozenset[str] = frozenset({
    "recommendations", "recommendation", "references", "acknowledgments",
    "acknowledgements", "conflict of interest", "disclosure", "funding",
    "abbreviations", "appendix", "introduction", "background",
    "abstract", "foreword", "preface", "table of contents", "contents",
    "figure", "table", "glossary", "summary", "key messages",
    "preamble", "overview", "annex", "annexe", "annex 1", "annex 2",
})

_MD_TABLE_RE = re.compile(
    r"(?m)^(\|[^\n]+\|\n)(\|[-: |]+\|\n)((?:\|[^\n]*\|\n)*)",
)

_HEADING_RE = re.compile(r"(?m)^(#{1,4})\s+(.+)$")

# Step-up protocol: title-case "Step 1 / Step 2 / Step 3" as used in drug titration ladders.
# Deliberately NOT re.I — all-caps "STEP 1: AGREEMENT AND APPOINTMENTS" are
# administrative process steps and must not be tagged with antihypertensive_ladder.
_TREATMENT_STEP_RE = re.compile(
    r"\bStep\s+(?:[1-9]|one|two|three|four|five)\b",
)

# BP threshold values used as clinical decision triggers
_BP_THRESHOLD_RE = re.compile(
    r"(?:"
    r"(?:systolic|SBP|diastolic|DBP|blood\s+pressure|BP)\s*"
    r"(?:≥|>=|>|≤|<=|<|of\s+at\s+least|above|below|greater\s+than|less\s+than)\s*"
    r"\d{2,3}(?:\s*/\s*\d{2,3})?\s*mmHg"
    r"|"
    r"\d{3}/\d{2,3}\s*mmHg"
    r"|"
    r"(?:≥|>=|>)\s*140\b"
    r"|"
    r"(?:≥|>=|>)\s*130\b"
    r"|"
    r"target\s+(?:BP|blood\s+pressure|systolic|diastolic)"
    r")",
    re.I,
)

# CVD risk scoring references
_CVD_RISK_RE = re.compile(
    r"(?:"
    r"10.year.(?:CVD|cardiovascular|risk)|"
    r"ten.year.(?:CVD|cardiovascular|risk)|"
    r"absolute.(?:CVD|cardiovascular).risk|"
    r"CVD.risk.(?:score|chart|categor|level|predict)|"
    r"WHO.ISH.risk|"
    r"risk.(?:chart|table|categor|stratif)|"
    r"(?:very.high|high|moderate|low).(?:CVD|cardiovascular).risk|"
    r"(?:very.high|high|moderate|low).risk.(?:categor|group|patient)"
    r")",
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
    return "hypertension, CVD_management, general"


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
            f"population=\"hypertension primary_care\" "
            f"year={YEAR} -->"
        )
        return f"{hashes} {title}{comment}"

    return _HEADING_RE.sub(_replacer, md)


def _annotate_treatment_protocol_steps(md: str) -> str:
    """
    Prepend a rag_metadata comment before lines containing a step number in an
    antihypertensive titration ladder (Step 1 / Step 2 / Step 3 …).

    chunk_note: keep_atomic_large_window instructs the chunker to use a larger
    window for this section so adjacent protocol steps are not split into
    separate vector nodes — a partial step ladder is clinically dangerous.
    """
    out: list[str] = []
    for line in md.splitlines():
        if _TREATMENT_STEP_RE.search(line) and len(line.strip()) > 20:
            out.append(
                f"<!-- rag_metadata source={SOURCE_KEY} "
                f"topic_tags=\"treatment_protocol, step_up_therapy, antihypertensive_ladder, hearts_module_E\" "
                f"chunk_note=\"keep_atomic_large_window\" "
                f"population=\"hypertension primary_care\" -->"
            )
        out.append(line)
    return "\n".join(out)


def _annotate_bp_decision_thresholds(md: str) -> str:
    """
    Prepend a rag_metadata comment before lines carrying specific BP threshold
    values used as clinical decision points (initiate/intensify/target).

    safety_critical=true signals to the retrieval engine that these lines must
    be returned verbatim and must not be paraphrased or truncated in RAG output.
    """
    out: list[str] = []
    for line in md.splitlines():
        if _BP_THRESHOLD_RE.search(line) and len(line.strip()) > 30:
            out.append(
                f"<!-- rag_metadata source={SOURCE_KEY} "
                f"topic_tags=\"BP_threshold, hypertension_management, treatment_initiation, hearts_module_E\" "
                f"safety_critical=true "
                f"population=\"hypertension primary_care\" -->"
            )
        out.append(line)
    return "\n".join(out)


def _annotate_cvd_risk_scoring(md: str) -> str:
    """
    Prepend a rag_metadata comment before lines referencing 10-year CVD risk
    scores, WHO/ISH risk charts, or risk categories.

    chunk_note: keep_atomic_large_window prevents risk chart rows from being
    separated from their row-header context (a risk percentage without its
    row label is meaningless to the LLM).
    """
    out: list[str] = []
    for line in md.splitlines():
        if _CVD_RISK_RE.search(line) and len(line.strip()) > 30:
            out.append(
                f"<!-- rag_metadata source={SOURCE_KEY} "
                f"topic_tags=\"CVD_risk, risk_stratification, WHO_ISH_chart, hearts_module_R\" "
                f"chunk_note=\"keep_atomic_large_window\" "
                f"population=\"hypertension primary_care\" -->"
            )
        out.append(line)
    return "\n".join(out)


def convert_document(pdf_path: Path) -> str:
    """Convert the WHO HEARTS PDF to clean RAG-ready Markdown via Docling."""
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

    # Annotation passes — purely additive, no content is dropped or modified
    # Order: treatment steps → BP thresholds → CVD risk → section metadata
    md = _annotate_treatment_protocol_steps(md)
    md = _annotate_bp_decision_thresholds(md)
    md = _annotate_cvd_risk_scoring(md)
    md = _inject_section_metadata(md)

    return md


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    if not PDF_PATH.exists():
        print(f"[ERROR] PDF not found: {PDF_PATH.resolve()}")
        sys.exit(1)

    print("WHO HEARTS Technical Package — Docling extractor")
    print(f"  Input  : {PDF_PATH.resolve()}")
    print(f"  Output : {OUT_FILE.resolve()}")
    print()
    print("  Converting ... (expect 3–8 min on CPU)", flush=True)

    try:
        md = convert_document(PDF_PATH)
    except Exception as exc:
        print(f"[ERROR] — {exc}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    full_md = RAG_HEADER + md

    # Quality signals
    step_annotations   = full_md.count("antihypertensive_ladder")
    bp_annotations     = full_md.count("safety_critical=true")
    risk_annotations   = full_md.count("WHO_ISH_chart")
    section_meta       = full_md.count(f"source={SOURCE_KEY} section=")
    table_count        = full_md.count("| --- |")
    bp_threshold_refs  = len(re.findall(r"\b(?:140|130|120)\s*/?\s*(?:90|80)?\s*mmHg", full_md, re.I))
    step_refs          = len(re.findall(r"\bStep\s+[1-5]\b", full_md, re.I))
    cvd_risk_refs      = len(re.findall(r"\b10.year.(?:CVD|cardiovascular|risk)\b", full_md, re.I))

    print(
        f"\n  OK  ({len(full_md):,} chars, ~{table_count} tables)"
    )
    print(f"  BP threshold refs            : {bp_threshold_refs}")
    print(f"  Treatment step refs          : {step_refs}")
    print(f"  10-year CVD risk refs        : {cvd_risk_refs}")
    print(f"  Step-up ladder annotations   : {step_annotations}")
    print(f"  BP threshold annotations     : {bp_annotations}")
    print(f"  CVD risk chart annotations   : {risk_annotations}")
    print(f"  Section metadata annotations : {section_meta}")

    OUT_FILE.write_text(full_md, encoding="utf-8")
    print(f"\n  Saved : {OUT_FILE.resolve()}")
    print(f"  Total chars : {len(full_md):,}")


if __name__ == "__main__":
    main()
