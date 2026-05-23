"""Chunker pipeline runner.

Reads corpus_manifest.json, finds each source's parsed markdown file in
parsed/, runs the appropriate chunker, and writes JSONL output to data/chunks/.

Usage:
    python ingestion/chunkers/run.py                   # all sources
    python ingestion/chunkers/run.py RSSDI_2022        # single source
    python ingestion/chunkers/run.py --dry-run         # print counts, no files
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from ingestion.chunkers.food_table import chunk_food_table_source
from ingestion.chunkers.narrative import chunk_narrative_source
from ingestion.chunkers.page_window import chunk_page_window_source
from ingestion.chunkers.recommendation import chunk_recommendation_source

# parsed/ filenames for each source (maps source key → parsed md filename)
_PARSED_FILES: dict[str, str] = {
    "RSSDI_2022":                      "RSSDI_2022_docling.md",
    "ICMR_STW_2024":                   "ICMR_STW_2024_docling.md",
    "ADA_2026":                        "ADA_2026_docling.md",
    "ICMR_NIN":                        "ICMR_NIN_docling.md",
    "Anoop_Misra_South_Asian_Nutrition": "Anoop_Misra_docling.md",
    "KDIGO_2022_DM_CKD":               "KDIGO_2022_DM_CKD_docling.md",
    "IDF_DAR":                         "IDF_DAR_Ramadan_docling.md",
    "ESC_2023_CV_DM":                  "ESC_2023_CVD_DM_docling.md",
    "WHO_HEARTS":                      "WHO_HEARTS_docling.md",
    "Telemedicine_Guidelines_2020":    "Telemedicine_Guidelines_India_2020.md",
}

_CHUNKER_MAP = {
    "recommendation": chunk_recommendation_source,
    "narrative":      chunk_narrative_source,
    "page_window":    chunk_page_window_source,
    "food_table":     chunk_food_table_source,
}


def _load_manifest(manifest_path: Path) -> list[dict]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries: list[dict] = []
    for tier_list in data.values():
        entries.extend(tier_list)
    return entries


def run_source(entry: dict, parsed_dir: Path, chunks_dir: Path, dry_run: bool = False) -> int:
    source = entry["source"]
    chunker_type = entry.get("chunker", "recommendation")
    year = entry.get("year", 0)
    retrieval_tier = entry.get("retrieval_tier", "core")
    condition_trigger = entry.get("condition_trigger") or None
    india_specific = entry.get("india_specific", True)

    md_filename = _PARSED_FILES.get(source)
    if not md_filename:
        print(f"  [{source}] WARNING: no parsed file mapping — skipping")
        return 0

    md_path = parsed_dir / md_filename
    if not md_path.exists():
        print(f"  [{source}] WARNING: {md_path} not found — skipping")
        return 0

    chunker_fn = _CHUNKER_MAP.get(chunker_type)
    if not chunker_fn:
        print(f"  [{source}] WARNING: unknown chunker '{chunker_type}' — skipping")
        return 0

    chunks = chunker_fn(
        md_path=md_path,
        source=source,
        year=year,
        retrieval_tier=retrieval_tier,
        condition_trigger=condition_trigger,
        india_specific=india_specific,
    )

    # Deduplicate by chunk_id — keeps first occurrence.
    # Duplicate chunk_ids arise when the same clinical text appears multiple times
    # in the source PDF (e.g., repeated safety thresholds, summary + detail sections).
    # The content is genuinely identical; text_hash dedup at upsert handles the rest.
    seen_ids: set[str] = set()
    deduped: list = []
    for chunk in chunks:
        if chunk.chunk_id not in seen_ids:
            seen_ids.add(chunk.chunk_id)
            deduped.append(chunk)
    dropped = len(chunks) - len(deduped)

    if not dry_run:
        chunks_dir.mkdir(parents=True, exist_ok=True)
        out_path = chunks_dir / f"{source}.jsonl"
        with out_path.open("w", encoding="utf-8") as f:
            for chunk in deduped:
                f.write(chunk.to_json() + "\n")

    return len(deduped)


def main(targets: list[str] | None = None, dry_run: bool = False) -> None:
    manifest_path = ROOT / "config" / "corpus_manifest.json"
    parsed_dir = ROOT / "parsed"
    chunks_dir = ROOT / "data" / "chunks"

    entries = _load_manifest(manifest_path)
    if targets:
        entries = [e for e in entries if e["source"] in targets]

    total = 0
    print(f"Chunker pipeline — {'DRY RUN' if dry_run else 'writing to ' + str(chunks_dir)}")
    print(f"{'Source':<45} {'Chunker':<18} {'Chunks':>7}")
    print("-" * 74)

    for entry in entries:
        source = entry["source"]
        chunker_type = entry.get("chunker", "recommendation")
        n = run_source(entry, parsed_dir, chunks_dir, dry_run=dry_run)
        print(f"  {source:<43} {chunker_type:<18} {n:>7,}")
        total += n

    print("-" * 74)
    print(f"  {'TOTAL':<43} {'':18} {total:>7,}")
    if not dry_run and total > 0:
        print(f"\nChunk JSONL files written to: {chunks_dir.resolve()}")


if __name__ == "__main__":
    args = sys.argv[1:]
    dry = "--dry-run" in args
    source_args = [a for a in args if not a.startswith("--")]
    main(targets=source_args or None, dry_run=dry)
