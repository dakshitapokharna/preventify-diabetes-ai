"""
db.py — pgvector connection, table setup, and upsert logic.

Table: preventify_corpus on Neon (PostgreSQL + pgvector extension)
Connection: POSTGRES_URL in .env at project root

NOTE on env var name:
  settings.py field is `postgres_url` → pydantic-settings maps to POSTGRES_URL env var.
  Your .env file must use:
      POSTGRES_URL=postgresql://user:pass@host/preventify?sslmode=require
  NOT DATABASE_URL (that would not be picked up by settings.postgres_url).

Schema alignment with reference docs:
  - 14 chunk metadata fields from CHUNKING_LOGIC.md all stored as columns
  - embedding: vector(1024) — matches bge-large-en-v1.5 output dimension
  - chunk_id: UNIQUE — used as the dedup/upsert key for --retry-failed
  - inserted_at: audit trail (SaMD Class B requires full logging)

Known metadata gaps (chunker-level, not fixed here):
  - population_scope (T1/T2/GDM) — in reference doc spec, missing from chunks
  - age_scope (adult/elderly/pediatric) — in reference doc spec, missing from chunks
  - topic_tag (glycemic/renal/foot/etc.) — in reference doc spec, missing from chunks
  These must be added in a future chunker annotation pass, not in the embedder.
"""

import numpy as np
import psycopg2
from pgvector.psycopg2 import register_vector


# ── Connection ──────────────────────────────────────────────────────────────

def get_connection(postgres_url: str):
    """
    Open a psycopg2 connection, enable the pgvector extension, then register
    the vector type adapter so numpy arrays can be passed as vector columns.

    register_vector() must run AFTER the extension exists — it looks up the
    vector OID in pg_type at call time. On a fresh Neon DB the extension is
    not pre-installed, so we CREATE EXTENSION first, then register.
    """
    conn = psycopg2.connect(postgres_url)
    # Enable extension before registering the type — safe to re-run (IF NOT EXISTS)
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        conn.commit()
    register_vector(conn)
    return conn


# ── Schema ───────────────────────────────────────────────────────────────────

def ensure_table(conn) -> None:
    """
    Create preventify_corpus table and indexes if they don't exist.
    Safe to re-run — uses CREATE TABLE IF NOT EXISTS and CREATE INDEX IF NOT EXISTS.
    """
    with conn.cursor() as cur:
        # Main corpus table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS preventify_corpus (
                id                SERIAL PRIMARY KEY,
                chunk_id          TEXT UNIQUE NOT NULL,      -- dedup + retry anchor
                source            TEXT NOT NULL,             -- e.g. RSSDI_2022
                year              INT,
                section_title     TEXT,
                text              TEXT NOT NULL,             -- full text incl. context header
                embedding         vector(1024),              -- bge-large-en-v1.5 output
                retrieval_tier    TEXT,                      -- core | triggered | compliance
                condition_trigger TEXT,                      -- null | ckd | cardio | ramadan | hypertension
                india_specific    BOOLEAN,                   -- true = RSSDI/ICMR sources
                kerala_food       BOOLEAN,                   -- true = ICMR-NIN Kerala food rows
                safety_critical   BOOLEAN,
                grade_priority    INT,                       -- 1 (strongest) → 5 (consensus)
                meal_context      TEXT,
                token_estimate    INT,
                text_hash         TEXT,
                inserted_at       TIMESTAMPTZ DEFAULT NOW()
            );
        """)

        # Metadata indexes — used by pre-filter before vector similarity search
        indexes = [
            ("source",            "source"),
            ("retrieval_tier",    "retrieval_tier"),
            ("condition_trigger", "condition_trigger"),
            ("india_specific",    "india_specific"),
            ("kerala_food",       "kerala_food"),
            ("safety_critical",   "safety_critical"),
            ("grade_priority",    "grade_priority"),
            ("text_hash",         "text_hash"),
        ]
        for name, col in indexes:
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_corpus_{name} "
                f"ON preventify_corpus ({col});"
            )

        conn.commit()
    print("  Table preventify_corpus and indexes verified.")


# ── Write operations ─────────────────────────────────────────────────────────

def delete_source(conn, source: str) -> int:
    """
    Delete all existing rows for a source before re-embedding.
    Used when running: python ingestion/embedder/run.py RSSDI_2022
    Returns the number of rows deleted.
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM preventify_corpus WHERE source = %s RETURNING id;",
            (source,)
        )
        # fetchall() must be called before reading rowcount — psycopg2 returns -1
        # for queries with RETURNING until the result set is fully consumed.
        deleted = len(cur.fetchall())
        conn.commit()
    return deleted


def upsert_chunk(conn, chunk: dict, embedding: np.ndarray) -> None:
    """
    Insert a single chunk with its embedding.
    ON CONFLICT (chunk_id) DO UPDATE — used by --retry-failed to insert
    a previously-failed chunk at its correct position (matched by chunk_id).

    The chunk lands in the correct logical position regardless of when it is
    inserted — retrieval uses metadata filters + vector similarity, not row order.
    """
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO preventify_corpus (
                chunk_id, source, year, section_title, text, embedding,
                retrieval_tier, condition_trigger, india_specific, kerala_food,
                safety_critical, grade_priority, meal_context, token_estimate, text_hash
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            ON CONFLICT (chunk_id) DO UPDATE SET
                embedding         = EXCLUDED.embedding,
                source            = EXCLUDED.source,
                year              = EXCLUDED.year,
                section_title     = EXCLUDED.section_title,
                text              = EXCLUDED.text,
                retrieval_tier    = EXCLUDED.retrieval_tier,
                condition_trigger = EXCLUDED.condition_trigger,
                india_specific    = EXCLUDED.india_specific,
                kerala_food       = EXCLUDED.kerala_food,
                safety_critical   = EXCLUDED.safety_critical,
                grade_priority    = EXCLUDED.grade_priority,
                meal_context      = EXCLUDED.meal_context,
                token_estimate    = EXCLUDED.token_estimate,
                text_hash         = EXCLUDED.text_hash;
                -- inserted_at intentionally excluded from DO UPDATE:
                -- preserves the original ingestion timestamp on retry,
                -- required for SaMD Class B audit trail integrity.
        """, (
            chunk["chunk_id"],
            chunk["source"],
            chunk.get("year"),
            chunk.get("section_title"),
            chunk["text"],
            embedding,                       # numpy array — pgvector adapter handles conversion
            chunk.get("retrieval_tier"),
            chunk.get("condition_trigger"),
            chunk.get("india_specific"),
            chunk.get("kerala_food"),
            chunk.get("safety_critical"),
            chunk.get("grade_priority"),
            chunk.get("meal_context"),
            chunk.get("token_estimate"),
            chunk.get("text_hash"),
        ))
        conn.commit()
