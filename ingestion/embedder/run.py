"""
run.py — Embedder entry point.

Reads chunks from data/chunks/*.jsonl, embeds each chunk's `text` field
with bge-large-en-v1.5 (batch size 32), upserts to preventify_corpus on Neon.

Usage:
    python ingestion/embedder/run.py                   # all 10 sources
    python ingestion/embedder/run.py RSSDI_2022        # single source (delete + re-embed)
    python ingestion/embedder/run.py --dry-run         # count only, no DB writes
    python ingestion/embedder/run.py --retry-failed    # retry chunks in logs/embed_failures.jsonl

Failure recovery:
    Failed chunks are written to logs/embed_failures.jsonl with full chunk JSON.
    Run --retry-failed to re-embed them and upsert at the correct position in the DB.
    Recovered chunks are removed from the log; still-failing chunks stay with updated error.

Re-run behaviour (single source):
    Deletes all existing rows for that source first, then re-inserts everything fresh.
    This is the correct approach for guideline updates (e.g. ADA 2027 replacing ADA 2026).
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

# Load .env before importing settings
from dotenv import load_dotenv
load_dotenv()

from config.settings import settings

# Apply HF_HOME before any HuggingFace import touches the cache path.
# C: drive is nearly full (~300 MB free); all model downloads must go to D:.
# Value comes from HF_HOME in .env (default: D:\hf_cache via settings.hf_home).
import os as _os
_os.environ.setdefault("HF_HOME", settings.hf_home)
_os.environ.setdefault("TRANSFORMERS_CACHE", settings.hf_home)
from ingestion.embedder.db import get_connection, ensure_table, delete_source, upsert_chunk
from ingestion.embedder.embed import load_model, embed_texts


# ── Paths ────────────────────────────────────────────────────────────────────

CHUNKS_DIR    = settings.chunks_dir           # data/chunks/
FAILURES_LOG  = Path("logs/embed_failures.jsonl")
BATCH_SIZE    = 32                            # safe for CPU, ~2-4 GB RAM


# ── Failure log helpers ───────────────────────────────────────────────────────

def load_failures() -> list[dict]:
    """Read all entries from the failures log. Returns [] if file doesn't exist."""
    if not FAILURES_LOG.exists():
        return []
    with open(FAILURES_LOG, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def save_failures(failures: list[dict]) -> None:
    """Overwrite the failures log with the current list."""
    FAILURES_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(FAILURES_LOG, "w", encoding="utf-8") as f:
        for entry in failures:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def make_failure_entry(chunk: dict, error: Exception) -> dict:
    """
    Build a failure log entry. Stores the FULL chunk JSON so --retry-failed
    can re-embed and upsert without reading the original JSONL file again.
    """
    return {
        "chunk_id":  chunk["chunk_id"],
        "source":    chunk["source"],
        "error":     str(error),
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "chunk":     chunk,         # all 14 fields — self-contained for retry
    }


# ── Core embed + upsert loop ─────────────────────────────────────────────────

def embed_and_upsert(
    chunks: list[dict],
    model,
    conn,
    dry_run: bool = False,
    desc: str = "Embedding",
) -> tuple[int, list[dict]]:
    """
    Embed chunks in batches of BATCH_SIZE and upsert each to the DB.

    Returns:
        inserted  — number of chunks successfully inserted / would-insert (dry_run)
        failures  — list of failure log entries for chunks that errored
    """
    inserted  = 0
    failures  = []

    for i in tqdm(range(0, len(chunks), BATCH_SIZE), desc=desc, unit="batch"):
        batch = chunks[i : i + BATCH_SIZE]
        texts = [c["text"] for c in batch]

        # ── Embed the batch ──────────────────────────────────────────────────
        try:
            embeddings = embed_texts(model, texts)
        except Exception as exc:
            # Entire batch failed to embed — log every chunk in the batch
            for chunk in batch:
                failures.append(make_failure_entry(chunk, exc))
            continue

        # ── Upsert each chunk individually ───────────────────────────────────
        for chunk, embedding in zip(batch, embeddings):
            if dry_run:
                inserted += 1
                continue
            try:
                upsert_chunk(conn, chunk, embedding)
                inserted += 1
            except Exception as exc:
                failures.append(make_failure_entry(chunk, exc))
                # rollback clears the ABORTED transaction state on the connection.
                # Without this, every subsequent upsert_chunk call on the same
                # connection raises InFailedSqlTransaction, killing all remaining chunks.
                try:
                    conn.rollback()
                except Exception:
                    pass  # connection may already be dead; next upsert will catch it

    return inserted, failures


# ── Source-level run ──────────────────────────────────────────────────────────

def run_source(
    source_name: str,
    model,
    conn,
    dry_run: bool = False,
) -> tuple[int, list[dict]]:
    """
    Load a single source's JSONL, delete existing rows, embed + upsert.
    Returns (inserted_count, failure_entries).
    """
    jsonl_path = CHUNKS_DIR / f"{source_name}.jsonl"
    if not jsonl_path.exists():
        print(f"  [SKIP] {jsonl_path} not found — check data/chunks/")
        return 0, []

    # Load all chunks
    chunks = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                chunks.append(json.loads(line))

    if not chunks:
        print(f"  [SKIP] {jsonl_path} is empty")
        return 0, []

    # Delete existing rows for this source before re-inserting
    # (correct behaviour for guideline updates — e.g. ADA 2027 replaces ADA 2026)
    if not dry_run:
        deleted = delete_source(conn, source_name)
        if deleted > 0:
            print(f"  Deleted {deleted} existing rows for {source_name}")

    print(f"  {len(chunks)} chunks to embed")
    inserted, failures = embed_and_upsert(
        chunks, model, conn,
        dry_run=dry_run,
        desc=f"  {source_name}",
    )
    return inserted, failures


# ── --retry-failed mode ───────────────────────────────────────────────────────

def run_retry_failed(model, conn, dry_run: bool = False) -> None:
    """
    Read logs/embed_failures.jsonl, re-embed each chunk, upsert by chunk_id.

    ON CONFLICT (chunk_id) DO UPDATE in upsert_chunk places the chunk at the
    correct logical position regardless of when it is re-inserted — retrieval
    uses metadata filters + vector similarity, not physical row order.

    After the run:
      - Successfully recovered entries are removed from the log.
      - Still-failing entries stay in the log with updated error + timestamp.
    """
    failures = load_failures()
    if not failures:
        print("No entries in logs/embed_failures.jsonl — nothing to retry.")
        return

    print(f"Retrying {len(failures)} failed chunk(s)...")

    still_failing = []
    recovered     = 0

    for entry in tqdm(failures, desc="Retrying", unit="chunk"):
        chunk = entry["chunk"]
        try:
            embeddings = embed_texts(model, [chunk["text"]])
            if not dry_run:
                upsert_chunk(conn, chunk, embeddings[0])
            recovered += 1
        except Exception as exc:
            # Update error and timestamp in-place, keep in log
            entry["error"]     = str(exc)
            entry["failed_at"] = datetime.now(timezone.utc).isoformat()
            still_failing.append(entry)

    if not dry_run:
        # Only write back to the log on a real run.
        # A --dry-run embeds in memory but never upserts, so chunks are not
        # actually recovered — the log must not be modified.
        save_failures(still_failing)

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n{prefix}Recovered: {recovered} | Still failing: {len(still_failing)}")
    if dry_run and recovered:
        print(f"  Failure log NOT modified — no DB writes were made (dry-run).")
    if still_failing:
        print(f"  Still-failing chunks remain in {FAILURES_LOG}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Embed clinical chunks and upsert to pgvector (preventify_corpus)"
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="Single source name to embed, e.g. RSSDI_2022. Omit to run all sources.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count what would be inserted without writing to the DB.",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Re-try chunks logged in logs/embed_failures.jsonl.",
    )
    args = parser.parse_args()

    # Mutual exclusion
    if args.retry_failed and args.source:
        print("Error: cannot use --retry-failed with a source name. Pick one.")
        sys.exit(1)

    # Load model (downloads ~1.3 GB on first run, cached after)
    model = load_model(settings.embedding_model)

    # Connect to Neon — skipped entirely for --dry-run so the script works
    # without a .env file (e.g. counting chunks on a fresh checkout, CI checks).
    conn = None
    if not args.dry_run:
        print(f"\nConnecting to Neon postgres...")
        try:
            conn = get_connection(settings.postgres_url)
        except Exception as exc:
            print(f"  Connection failed: {exc}")
            print("  Check POSTGRES_URL in your .env file.")
            sys.exit(1)
        print("  Connected.")
        ensure_table(conn)
    else:
        print("\n[DRY RUN] Skipping DB connection -- no writes will be made.")

    # ── Retry mode ────────────────────────────────────────────────────────────
    if args.retry_failed:
        run_retry_failed(model, conn, dry_run=args.dry_run)
        if conn:
            conn.close()
        return

    # ── Normal embed mode ─────────────────────────────────────────────────────
    if args.source:
        sources = [args.source]
    else:
        # All JSONL files in data/chunks/, sorted alphabetically
        sources = sorted(p.stem for p in CHUNKS_DIR.glob("*.jsonl"))

    if not sources:
        print(f"No JSONL files found in {CHUNKS_DIR}. Run the chunker first.")
        sys.exit(1)

    total_inserted = 0
    all_new_failures: list[dict] = []

    for source in sources:
        print(f"\n-- {source}")
        inserted, failures = run_source(source, model, conn, dry_run=args.dry_run)
        total_inserted    += inserted
        all_new_failures  += failures
        status = "would insert" if args.dry_run else "inserted"
        print(f"  OK {inserted} {status} | {len(failures)} failed")

    # Merge new failures into the log (don't clobber existing failures from other sources)
    if all_new_failures:
        existing = load_failures()
        new_ids  = {f["chunk_id"] for f in all_new_failures}
        merged   = [f for f in existing if f["chunk_id"] not in new_ids] + all_new_failures
        save_failures(merged)
        print(f"\nWARN: {len(all_new_failures)} chunk(s) failed -- logged to {FAILURES_LOG}")
        print(f"   Re-run with --retry-failed to recover them.")

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{prefix}Done -- {total_inserted} total | {len(all_new_failures)} failed")
    if conn:
        conn.close()


if __name__ == "__main__":
    main()
