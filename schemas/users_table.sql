-- Preventify users table — Neon PostgreSQL (pgvector instance)
-- This table holds all three profile layers for every patient.
-- All signal writes go through signal_writer.py — never write directly.
--
-- Testing phase:  user_id = UUID (generated per tester session)
-- Production:     user_id = WhatsApp number (permanent, never changes)
--
-- Run once on a fresh Neon database. The preventify_corpus table (vector store)
-- lives in the same database — see CLAUDE.md RAG System Design for its DDL.

-- ─────────────────────────────────────────────────────────────────────────────
-- Extension (already created for pgvector — ensure it exists)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS vector;


-- ─────────────────────────────────────────────────────────────────────────────
-- Core users table
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (

    -- ── Layer 1: Identity ────────────────────────────────────────────────────
    user_id             TEXT        PRIMARY KEY,            -- UUID (testing) | WhatsApp number (prod)
    name                TEXT,                               -- collected at consent or mentioned naturally
    age                 INT,                                -- asked once in first session; never re-asked
    first_contact_date  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    location_hint       TEXT        NOT NULL DEFAULT '',    -- city/district inferred from conversation

    -- ── Layer 2: Clinical Profile ────────────────────────────────────────────
    -- All clinical fields use merge rules — never overwrite blindly (see signal_writer.py)
    diabetes_type       TEXT        NOT NULL DEFAULT '',    -- T1DM | T2DM | GDM | prediabetes | suspected | ''
    condition_flags     TEXT[]      NOT NULL DEFAULT '{}',  -- ckd | cardio | ramadan | hypertension — permanent
    medications_mentioned TEXT[]    NOT NULL DEFAULT '{}',  -- controlled vocab — append only
    insulin_user        BOOLEAN     NOT NULL DEFAULT FALSE, -- once TRUE, never reset to FALSE
    complications_mentioned TEXT[]  NOT NULL DEFAULT '{}',  -- controlled vocab — append only
    highest_qds_ever    INT         NOT NULL DEFAULT 0,     -- peak QDS across all sessions
    escalation_history  JSONB       NOT NULL DEFAULT '[]',  -- [{date, tier, trigger, outcome}]

    -- ── Layer 3: Engagement + Lead Capture ──────────────────────────────────
    lifetime_score      FLOAT       NOT NULL DEFAULT 0.0,   -- QDS-based, with volume decay + recency weight
    total_sessions      INT         NOT NULL DEFAULT 0,
    total_messages      INT         NOT NULL DEFAULT 0,
    last_session_date   DATE,
    consent_status      TEXT        NOT NULL DEFAULT 'not_yet',  -- not_yet | given | declined
    consent_timestamp   TIMESTAMPTZ,
    consent_declined_at TIMESTAMPTZ,
    lead_status         TEXT        NOT NULL DEFAULT 'new_lead', -- new_lead | contacted | qualified | converted | closed

    -- ── Short persistent memory ──────────────────────────────────────────────
    -- Compressed at end of every session by Gemini 2.0 Flash (~100 tokens).
    -- Passed to Phase 2 on every turn to give clinical context without full profile.
    short_memory        TEXT        NOT NULL DEFAULT '',

    -- ── Audit ────────────────────────────────────────────────────────────────
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ─────────────────────────────────────────────────────────────────────────────
-- Indexes — all fields queried by Session Manager or lead capture logic
-- ─────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_users_consent_status  ON users (consent_status);
CREATE INDEX IF NOT EXISTS idx_users_lead_status      ON users (lead_status);
CREATE INDEX IF NOT EXISTS idx_users_last_session     ON users (last_session_date);
CREATE INDEX IF NOT EXISTS idx_users_lifetime_score   ON users (lifetime_score);
-- GIN indexes for array columns (needed for ANY() / @> array queries)
CREATE INDEX IF NOT EXISTS idx_users_condition_flags  ON users USING GIN (condition_flags);
CREATE INDEX IF NOT EXISTS idx_users_complications    ON users USING GIN (complications_mentioned);


-- ─────────────────────────────────────────────────────────────────────────────
-- session_turns table
-- Stores the last 5 turns of the current session in-memory during the session.
-- Persisted to this table so turns survive within the same session across async
-- message handlers (e.g. if the webhook fires again before the previous response
-- is sent). Cleared (deleted) at session end.
-- NOT a long-term conversation store — full history is never retained.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS session_turns (
    id              SERIAL      PRIMARY KEY,
    user_id         TEXT        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    session_id      TEXT        NOT NULL,   -- UUID per session; ties turns together
    turn_number     INT         NOT NULL,   -- 1–10 within session (5 patient + 5 bot)
    role            TEXT        NOT NULL,   -- 'patient' | 'bot'
    content         TEXT        NOT NULL,
    qds_score       INT,                    -- set on patient turns by Phase 1
    risk_tier       INT,                    -- set on patient turns by Risk Engine
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_session_turns_user_session
    ON session_turns (user_id, session_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- query_cache table
-- Application-layer cache for common pgvector ANN queries.
-- Most common patient queries (rice portions, HbA1c, chaaya) produce the same
-- top-20 chunk results. Cache the chunk_ids to skip the ANN search entirely.
-- See BOT_CONVERSATION_ARCHITECTURE.md Section 13.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS query_cache (
    query_hash      TEXT        PRIMARY KEY,    -- SHA256[:32] of enriched query string
    chunk_ids       TEXT[]      NOT NULL,       -- ordered list of chunk_id values from last ANN search
    condition_flags TEXT[]      NOT NULL DEFAULT '{}',  -- flags active when cache was written
    hit_count       INT         NOT NULL DEFAULT 1,
    cached_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL        -- NOW() + INTERVAL '24 hours' at write time
);

CREATE INDEX IF NOT EXISTS idx_query_cache_expires ON query_cache (expires_at);


-- ─────────────────────────────────────────────────────────────────────────────
-- Trigger: auto-update users.updated_at on any row change
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_users_updated_at ON users;
CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
