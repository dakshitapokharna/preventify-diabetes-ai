"""
One-shot script: apply annotation passes to existing parsed files
without re-running the expensive Docling/pdfplumber extractors.

Run:
    python _annotate_existing.py
"""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).parent
PARSED = ROOT / "parsed"

# ─────────────────────────────────────────────────────────────────────────────
# ADA 2026
# ─────────────────────────────────────────────────────────────────────────────

ADA_SOURCE = "ADA_2026"
ADA_YEAR = 2026
ADA_FILE = PARSED / "ADA_2026_docling.md"

ADA_RAG_HEADER = """\
<!-- rag_metadata
  source: ADA_2026
  title: ADA Standards of Care in Diabetes 2026
  citation: Diabetes Care 2026;49(Suppl. 1)
  year: 2026
  population: Adults and children with T1DM, T2DM, prediabetes, GDM; global with US focus
  topic_tags: T2DM, T1DM, glycemic_targets, drug_selection, elderly, CGM, hypoglycemia, CVD_risk, pregnancy, GDM, complication_screening, lifestyle, obesity, ADA_evidence_graded
  retrieval_tier: core
  condition_trigger: null
  india_specific: false
  age_scope: adult_and_paediatric
  evidence_grade: A/B/C/E
-->

"""

ADA_SECTION_TAG_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"glycem|HbA1c|A1c|target|control|fasting|postprandial|time.in.range|TIR", re.I),
     "glycemic_targets, HbA1c"),
    (re.compile(r"metformin|first.line|initial|monotherapy", re.I),
     "first_line_therapy, metformin"),
    (re.compile(r"sulfonyl|SU\b|glimep|glibenc|gliclaz", re.I),
     "sulfonylureas, drug_class"),
    (re.compile(r"DPP.4|gliptin|sitagliptin|vildagliptin|teneligliptin|alogliptin", re.I),
     "DPP4_inhibitors, drug_class"),
    (re.compile(r"SGLT2|gliflozin|dapagliflozin|empagliflozin|canagliflozin", re.I),
     "SGLT2_inhibitors, drug_class"),
    (re.compile(r"GLP.1|liraglutide|semaglutide|exenatide|dulaglutide|tirzepatide", re.I),
     "GLP1_agonists, drug_class"),
    (re.compile(r"insulin|basal|premix|bolus|NPH|degludec|glargine|aspart|lispro", re.I),
     "insulin, injectable_therapy"),
    (re.compile(r"thiazolidinedione|pioglitazone|TZD", re.I),
     "thiazolidinediones, drug_class"),
    (re.compile(r"obesity|weight|BMI|overweight|bariatric", re.I),
     "obesity, weight_management"),
    (re.compile(r"lifestyle|diet|nutrition|physical.activity|exercise|MNT|meal.plan", re.I),
     "lifestyle_modification, nutrition, physical_activity"),
    (re.compile(r"screen|complication|nephropathy|retinopathy|neuropathy|foot|fundus|UACR", re.I),
     "complication_screening, microvascular"),
    (re.compile(r"CKD|kidney|renal|creatinine|eGFR|dialysis|albuminuria", re.I),
     "CKD, renal_dosing, eGFR"),
    (re.compile(r"cardio|cardiovascular|CVD|heart|coronary|ASCVD|atherosclerosis|statin", re.I),
     "cardiovascular, CVD_risk"),
    (re.compile(r"BP|blood.pressure|hypertension|antihypertensive", re.I),
     "hypertension, blood_pressure"),
    (re.compile(r"lipid|dyslipidemia|cholesterol|statin|triglyceride|LDL|HDL", re.I),
     "dyslipidemia, lipids, statins"),
    (re.compile(r"hypoglycemia|low.sugar|low.glucose|hypoglycaemia|severe.hypo", re.I),
     "hypoglycemia, safety"),
    (re.compile(r"elder|geriatric|older.adult|frail|age[d>]\s*6[05]|S12", re.I),
     "elderly, geriatric"),
    (re.compile(r"pregnan|gestational|GDM|antenatal|lactation|postpartum", re.I),
     "pregnancy, GDM"),
    (re.compile(r"fast|ramadan|roza|religious", re.I),
     "fasting, ramadan, religious"),
    (re.compile(r"diagnos|criteria|classify|classification|prediabetes", re.I),
     "diagnosis, classification"),
    (re.compile(r"self.monitor|SMBG|CGM|continuous.glucose|flash.glucose|sensor", re.I),
     "glucose_monitoring, CGM, SMBG"),
    (re.compile(r"technolog|pump|closed.loop|AID|automated.insulin|device", re.I),
     "diabetes_technology, insulin_pump, CGM"),
    (re.compile(r"mental.health|depression|distress|anxiety|psychosoc|well.being", re.I),
     "mental_health, psychosocial"),
    (re.compile(r"social.determinant|SDOH|health.equity|disparit|access", re.I),
     "health_equity, SDOH"),
    (re.compile(r"child|paediatric|pediatric|adolescent|youth|school", re.I),
     "paediatric, children, adolescent"),
    (re.compile(r"hospital|inpatient|perioperative|critical.care|surgical", re.I),
     "inpatient, perioperative"),
    (re.compile(r"refer|escalat|specialist", re.I),
     "referral, escalation"),
    (re.compile(r"prevention|delay|prediabetes|risk.reduction|lifestyle.interv", re.I),
     "prevention, prediabetes"),
    (re.compile(r"smok|tobacco|alcohol|substance", re.I),
     "smoking, substance_use"),
]

_ADA_SKIP: frozenset[str] = frozenset({
    "recommendations", "recommendation", "references", "acknowledgments",
    "acknowledgements", "conflict of interest", "disclosure", "funding",
    "abbreviations", "appendix", "introduction", "background",
    "abstract", "foreword", "preface", "table of contents", "contents",
    "figure", "table", "glossary", "summary",
})

_HEADING_RE = re.compile(r"(?m)^(#{1,4})\s+(.+)$")
_ADA_GRADE_RE = re.compile(r"\s+([ABCE])\s*$")


def _ada_section_tags(h: str) -> str:
    for p, t in ADA_SECTION_TAG_MAP:
        if p.search(h):
            return t
    return "general"


def _ada_inject_section_metadata(md: str) -> str:
    def _rep(m: re.Match) -> str:
        hashes, title = m.group(1), m.group(2).strip()
        if title.rstrip(".").lower() in _ADA_SKIP:
            return f"{hashes} {title}"
        tags = _ada_section_tags(title)
        comment = (
            f"\n<!-- rag_metadata source={ADA_SOURCE} "
            f"section=\"{title}\" "
            f"topic_tags=\"{tags}\" "
            f"population=\"T2DM T1DM global\" "
            f"year={ADA_YEAR} -->"
        )
        return f"{hashes} {title}{comment}"
    return _HEADING_RE.sub(_rep, md)


def _ada_annotate_evidence_grades(md: str) -> str:
    out: list[str] = []
    for line in md.splitlines():
        if len(line.strip()) > 40 and line.strip().startswith("-"):
            m = _ADA_GRADE_RE.search(line)
            if m:
                g = m.group(1).upper()
                out.append(
                    f"<!-- rag_metadata source={ADA_SOURCE} "
                    f"evidence_grade=\"{g}\" "
                    f"topic_tags=\"recommendation, grade_{g}\" "
                    f"population=\"T2DM T1DM global\" -->"
                )
        out.append(line)
    return "\n".join(out)


def annotate_ada() -> None:
    print(f"ADA 2026 — in-place annotation of {ADA_FILE.name}")
    md = ADA_FILE.read_text(encoding="utf-8")

    # Prepend RAG header only if not already present
    if not md.startswith("<!-- rag_metadata"):
        md = ADA_RAG_HEADER + md

    md = _ada_inject_section_metadata(md)
    md = _ada_annotate_evidence_grades(md)

    ADA_FILE.write_text(md, encoding="utf-8")
    sec = md.count("rag_metadata source=")
    grade = md.count("evidence_grade=")
    print(f"  Section annotations : {sec}")
    print(f"  Grade annotations   : {grade}")
    print(f"  Total chars         : {len(md):,}")


# ─────────────────────────────────────────────────────────────────────────────
# KDIGO 2022
# ─────────────────────────────────────────────────────────────────────────────

KDIGO_SOURCE = "KDIGO_2022_DM_CKD"
KDIGO_YEAR = 2022
KDIGO_FILE = PARSED / "KDIGO_2022_DM_CKD_docling.md"

_KDIGO_GRADE_RE = re.compile(r"\(([12][ABCD])\)")
_CHAPTER_RE = re.compile(r"^(Chapter\s+\d+[:\.]?\s+.+)$", re.M | re.I)
_EGFR_THRESHOLD_RE = re.compile(
    r"eGFR\s*[<>≥≤]\s*\d+|eGFR\s+of\s+\d+|\beGFR\b.{0,40}\b(20|30|45|60)\b",
    re.I,
)

_CHAPTER_TAG_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"comprehensive", re.I),
     "comprehensive_care, CKD_management, T2DM_CKD"),
    (re.compile(r"glycem", re.I),
     "glycemic_targets, HbA1c, CGM, glucose_monitoring"),
    (re.compile(r"lifestyle", re.I),
     "lifestyle_modification, nutrition, physical_activity, CKD_diet"),
    (re.compile(r"glucose.lower|pharmacol|drug|therap", re.I),
     "drug_selection, SGLT2_inhibitors, GLP1_agonists, metformin, drug_dose_adjustment"),
    (re.compile(r"self.manag|education|team.based|integrated", re.I),
     "self_management, patient_education, team_care"),
]


def _kdigo_chapter_tags(text: str) -> str:
    for p, t in _CHAPTER_TAG_MAP:
        if p.search(text):
            return t
    return "CKD, T2DM_CKD, general"


def _kdigo_inject_chapter_metadata(md: str) -> str:
    def _rep(m: re.Match) -> str:
        chapter_line = m.group(1).strip()
        tags = _kdigo_chapter_tags(chapter_line)
        comment = (
            f"<!-- rag_metadata source={KDIGO_SOURCE} "
            f"section=\"{chapter_line}\" "
            f"topic_tags=\"{tags}\" "
            f"population=\"T2DM T1DM CKD\" "
            f"year={KDIGO_YEAR} -->\n"
        )
        return comment + chapter_line
    return _CHAPTER_RE.sub(_rep, md)


def _kdigo_annotate_recommendation_grades(md: str) -> str:
    out: list[str] = []
    for line in md.splitlines():
        if len(line.strip()) > 20:
            grades = _KDIGO_GRADE_RE.findall(line)
            if grades:
                primary = grades[0]
                strength = "strong" if primary.startswith("1") else "conditional"
                all_grades = ",".join(grades)
                out.append(
                    f"<!-- rag_metadata source={KDIGO_SOURCE} "
                    f"evidence_grade=\"{primary}\" "
                    f"all_grades=\"{all_grades}\" "
                    f"recommendation_strength=\"{strength}\" "
                    f"topic_tags=\"recommendation, grade_{primary}, CKD\" -->"
                )
        out.append(line)
    return "\n".join(out)


def _kdigo_annotate_egfr_thresholds(md: str) -> str:
    out: list[str] = []
    for line in md.splitlines():
        if len(line.strip()) > 40 and _EGFR_THRESHOLD_RE.search(line):
            out.append(
                f"<!-- rag_metadata source={KDIGO_SOURCE} "
                f"topic_tags=\"eGFR_threshold, drug_dose_adjustment, CKD, safety\" "
                f"safety_critical=true "
                f"chunk_note=\"zero_loss_standalone_node\" -->"
            )
        out.append(line)
    return "\n".join(out)


def annotate_kdigo() -> None:
    print(f"KDIGO 2022 — in-place annotation of {KDIGO_FILE.name}")
    md = KDIGO_FILE.read_text(encoding="utf-8")

    md = _kdigo_inject_chapter_metadata(md)
    md = _kdigo_annotate_recommendation_grades(md)
    md = _kdigo_annotate_egfr_thresholds(md)

    KDIGO_FILE.write_text(md, encoding="utf-8")
    chapters = md.count("rag_metadata source=") - 1  # minus doc header
    grade = md.count("evidence_grade=")
    safety = md.count("safety_critical=true")
    print(f"  Chapter annotations          : {chapters}")
    print(f"  Grade annotations            : {grade}")
    print(f"  eGFR safety_critical lines   : {safety}")
    print(f"  Total chars                  : {len(md):,}")


if __name__ == "__main__":
    print("=" * 60)
    annotate_ada()
    print()
    print("=" * 60)
    annotate_kdigo()
    print()
    print("Both files updated.")
