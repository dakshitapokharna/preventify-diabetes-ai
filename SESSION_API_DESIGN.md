# Session Manager + API + Frontend — Design Document

**Version:** 1.0  
**Date:** 2026-05-25  
**Status:** Design approved — ready to build  
**Depends on:** `engine/phase1.py` (orchestrator, built), all Neon tables (created)

---

## 1. Decisions Made

| Decision | Choice | Reason |
|----------|--------|--------|
| Framework | FastAPI | Async-native — works directly with asyncio pipeline; no threading hacks needed |
| Streaming | SSE via `fetch()` + `ReadableStream` | Simpler than WebSocket for one-way chat; browser EventSource only supports GET so we use `fetch()` with POST |
| Language (testing) | English only | No ASR/translation layer yet; added later after base model passes clinical sign-off |
| User identity | Browser-generated UUID, stored in `localStorage` | No login needed; persists across page refreshes; UUID replaced by WhatsApp number in production |
| Session end | After 10 patient turns | Triggers memory compression (Gemini Flash → `users.short_memory`); clean boundary |
| Chat history | Fresh on every page load | Simpler for testing; `session_turns` clears at session end |
| ML model loading | Once at server startup, held in `app.state` | 30s startup cost paid once; every request is fast after that; D: drive (HF_HOME already set) |
| DB connections | asyncpg pool, min=2 max=5 | Local dev; enough for concurrent test sessions |
| Rate limiting | 30 messages / hour / user_id | In-memory dict (no Redis needed for local); controls OpenRouter + Gemini API costs |
| Auth | None (open) | Localhost only during testing |
| Audit logging | Every turn from day one | SaMD compliance; starts from first test message |
| Deployment | Local only (localhost:8000) | Base model validation phase |
| Streaming fake vs real | Status events + buffered text stream | See Section 4 |
| Debug panel | QDS + intent, sources, fallback flags, risk tier | See Section 8 |

---

## 2. System Overview

```
Browser (localhost:8000)
    │
    │  POST /chat  {message, user_id, session_id}
    ▼
FastAPI app (api/app.py)
    │
    ├── Rate limiter check  (in-memory, 30/hr/user)
    │
    ├── Load user profile from DB  (asyncpg pool)
    │
    ├── Load session_turns from DB  (last 5 turns for this session)
    │
    ├── SSE stream begins → status event: "Looking up guidelines..."
    │
    ├── handle_phase1()  ←── engine/phase1.py (orchestrator)
    │       ├── run_phase1()
    │       ├── write_profile_signals()  (fire-and-forget)
    │       ├── run_phase2()  (RAG pipeline)
    │       └── build_response()
    │
    ├── SSE stream → status events during retrieval steps
    │
    ├── write_audit_log()  (conversation_audit_log)
    │
    ├── save_turn()  (session_turns table)
    │
    ├── check_session_end()  → if 10 patient turns → compress_memory()
    │
    └── SSE stream → response chunks → done event with metadata
```

---

## 3. File Structure

```
api/
├── app.py              ← FastAPI app, lifespan, routes
├── session_manager.py  ← per-turn logic: load profile, save turn, session end
├── rate_limiter.py     ← in-memory 30/hr/user rate limit
├── audit_logger.py     ← writes to conversation_audit_log
├── memory_compressor.py← Gemini Flash call to compress session into short_memory
└── routes/
    └── chat.py         ← POST /chat endpoint (SSE streaming response)

static/
├── index.html          ← chat UI
├── style.css           ← styles (white/teal, mobile-first)
└── app.js              ← fetch + ReadableStream + SSE parser + debug panel
```

---

## 4. Streaming Architecture

### Why status events + buffered text (not raw token streaming)

Raw token streaming from Gemini pipes each token directly to the browser. The problem: `check_constraints()` needs the **full text** to scan for dose/diagnosis violations. If we stream tokens before the constraint check, a violating response reaches the user before we can stop it.

**Solution: two-phase streaming**

```
Phase A — Progress events (while pipeline runs):
  {"type": "status", "text": "Retrieving relevant guidelines..."}
  {"type": "status", "text": "Found 5 sections from RSSDI 2022 and ADA 2026..."}
  {"type": "status", "text": "Generating response..."}

Phase B — After full response + constraint check passes:
  {"type": "chunk", "text": "Rice "}
  {"type": "chunk", "text": "in small "}
  {"type": "chunk", "text": "portions..."}
  ...
  {"type": "done", "meta": { ...debug fields... }}

If constraint violation:
  {"type": "error", "text": "I'm not able to answer that directly. Please consult your doctor."}
  {"type": "done", "meta": { ...debug fields... }}
```

Phase B streams the buffered text word-by-word at ~30ms delay — feels natural to the user. The constraint check happens between Phase A and Phase B, so violating text never reaches the browser.

### Clarifying question (context_sufficient=False)

```
  {"type": "clarify", "question": "How long have you had this problem?",
   "format": "buttons", "options": ["Less than a week", "1–4 weeks", "More than a month"]}
  {"type": "done", "meta": {...}}
```
Frontend renders option buttons instead of a text bubble.

---

## 5. API Contract

### POST /chat

**Request:**
```json
{
  "message": "Can I eat rice?",
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "session_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7"
}
```

**Response:** `Content-Type: text/event-stream`

Each line: `data: <json>\n\n`

**SSE event types:**

| type | fields | when |
|------|--------|------|
| `status` | `text` | Pipeline progress messages |
| `chunk` | `text` | One word/phrase of bot response |
| `clarify` | `question`, `format`, `options` | Phase 1 needs more context |
| `done` | `meta` object | Always last event |
| `error` | `text` | Rate limit, pipeline failure |

**`done.meta` object:**
```json
{
  "qds_score": 3,
  "intent": "healthy_eating",
  "risk_tier": 0,
  "sources": [
    {"source": "RSSDI_2022", "section": "Glycemic Targets", "grade": 1},
    {"source": "ADA_2026",   "section": "Nutrition Therapy", "grade": 2}
  ],
  "phase1_fallback": false,
  "phase2_fallback": false,
  "constraint_violation": false,
  "query_cache_hit": false,
  "session_turn_count": 3
}
```

**Source of each field:**
- `qds_score`, `intent`, `phase1_fallback` — from `phase1_output`
- `sources` — from `phase2_output["chunks_detail"]` (list of `{source, section, grade_priority}` dicts built inside `phase2_runner.py` from `top_5_chunks` — no extra DB lookup needed)
- `phase2_fallback` — from `phase2_output["_fallback"]`
- `constraint_violation` — from `phase2_output["constraint_violation"]`
- `query_cache_hit` — from `phase2_output["query_cache_hit"]`
- `risk_tier` — passed directly into the route handler
- `session_turn_count` — from session_manager after saving the turn

### GET /health
```json
{"status": "ok", "db": "connected", "models": "loaded"}
```

### GET /
Serves `static/index.html`

---

## 6. Session Manager — `api/session_manager.py`

Responsibilities per turn:

```python
async def process_turn(message, user_id, session_id, db_conn, app_state) -> TurnResult:

    # 1. Load user profile (or None for new user)
    profile = await load_user_profile(user_id, db_conn)

    # 2. Load session turns (last 5 for this session_id)
    session_turns = await load_session_turns(user_id, session_id, db_conn)

    # 3. Run pipeline
    result = await handle_phase1(
        message=message,
        session_turns=session_turns,
        user_profile=profile,
        user_id=user_id,
        db_conn=db_conn,
        embedder=app_state.embedder,
        reranker=app_state.reranker,
    )

    # 4. Save this turn (patient message + bot response)
    turn_number = await save_turn(user_id, session_id, message, result, db_conn)

    # 5. Check session end (10 patient turns)
    if turn_number >= 10:
        asyncio.create_task(
            end_session(user_id, session_id, db_conn, app_state.flash_model)
        )

    return result, turn_number
```

### Session end — `end_session()`
1. Load all turns for this `session_id`
2. Call Gemini Flash with memory compression prompt → ~100-token summary
3. Update `users.short_memory` with new summary
4. Delete rows from `session_turns` for this `session_id`
5. Increment `users.total_sessions`, update `users.last_session_date`

---

## 7. Rate Limiter — `api/rate_limiter.py`

In-memory. No Redis needed for local dev.

```python
# Structure: {user_id: deque of timestamps}
# Check: filter to last 3600s, if len >= 30 → reject
# On pass: append current timestamp
```

Returns `HTTP 429` with SSE `{"type": "error", "text": "You've sent too many messages. Please wait a few minutes."}` when limit hit.

---

## 8. Audit Logger — `api/audit_logger.py`

Writes one row to `conversation_audit_log` per turn. Called after `handle_phase1()` returns — never before (so we always have the full result).

Fields populated from:
- `phase1_output` → `qds_score`, `intent`, `phase1_fallback` (key: `_fallback`)
- `phase2_output` → `chunks_used`, `condition_flags_active`, `query_cache_hit`, `reranker_scores`, `phase2_fallback` (key: `_fallback`), `phase2_fallback_reason` (key: `_fallback_reason`), `constraint_violation`, `constraint_violations`
- `risk_tier` → `risk_tier`, `tier_3_subtype`
- Call context → `user_id`, `session_id`, `message_id` (UUID), `turn_number`, `patient_message`, `bot_response`, `response_type`

**Exact key mapping** (phase2_output key → audit log column):

| phase2_output key | audit log column |
|-------------------|-----------------|
| `_fallback` | `phase2_fallback` |
| `_fallback_reason` | `phase2_fallback_reason` |
| `chunks_used` | `chunks_used` |
| `condition_flags_active` | `condition_flags_active` |
| `query_cache_hit` | `query_cache_hit` |
| `reranker_scores` | `reranker_scores` |
| `constraint_violation` | `constraint_violation` |
| `constraint_violations` | `constraint_violations` |

`response_type` mapping:
- Phase 2 ran successfully → `"phase2_response"`
- Phase 1 asked clarifying question → `"clarifying_question"`
- Phase 1 fallback fired → `"phase1_fallback"`
- Phase 2 fallback fired → `"phase2_fallback"`
- Risk tier 3/4 → `"risk_escalation"`

---

## 9. Memory Compressor — `api/memory_compressor.py`

Gemini Flash call at session end.

**Input:** All turns from this session (patient + bot, in order)  
**Prompt:** "You are a clinical note compressor. Summarise this diabetes education conversation in 80–100 tokens. Include: detected diabetes type, medications mentioned, main concerns raised, complications mentioned. Format: plain sentences. No diagnosis. No doses."  
**Output:** ~100-token string → stored in `users.short_memory`

**Model:** `gemini-2.0-flash-001` via OpenRouter (same as Phase 1 — cheap call)

---

## 10. User Identity

**Frontend logic (app.js):**
```javascript
// On page load:
let userId = localStorage.getItem('preventify_user_id');
if (!userId) {
    userId = crypto.randomUUID();
    localStorage.setItem('preventify_user_id', userId);
}

// New session every page load:
const sessionId = crypto.randomUUID();
```

- `user_id` persists across page refreshes and browser restarts (until localStorage cleared)
- `session_id` is fresh every page load (no history shown — clean slate UX)
- First message from a new `user_id` → `signal_writer.py` creates the row via `INSERT ... ON CONFLICT DO NOTHING`

---

## 11. Frontend — `static/`

### Chat UI (index.html + style.css)

```
┌─────────────────────────────────────┐
│  🩺 Preventify Diabetes Educator    │  ← header, teal background
├─────────────────────────────────────┤
│                                     │
│   [Bot bubble] Hello! I'm here...   │
│                                     │
│        [User bubble] Can I eat rice?│
│                                     │
│   [Bot bubble — streaming...]       │
│   Rice in small portions...▌        │
│                                     │
│   [Clarify example:]                │
│   ┌──────────┐ ┌──────────────────┐ │
│   │Less than │ │1–4 weeks         │ │  ← option buttons
│   │a week    │ └──────────────────┘ │
│   └──────────┘                      │
│                                     │
├─────────────────────────────────────┤
│  ▼ Debug panel (collapsed by default│
│  QDS: 3 | Intent: healthy_eating    │
│  Sources: RSSDI 2022, ADA 2026      │
│  Risk tier: 0 | Fallback: none      │
├─────────────────────────────────────┤
│  [Type your question...]  [Send ▶]  │  ← input bar, sticky bottom
└─────────────────────────────────────┘
```

**Mobile-first:** Single column, input pinned to bottom, chat area scrolls. On desktop: chat stays max-width 720px, centered.

**Status messages:** Shown as small grey italic text in the bot bubble area while pipeline runs:
- *"Looking up guidelines..."*
- *"Found 5 sections from RSSDI 2022..."*
- *"Generating response..."*
Replaced by the actual response text once streaming starts.

### Debug panel (why these 4 fields)

| Field | Why |
|-------|-----|
| QDS + intent | Primary validation target for Dr. Rakesh — confirms Phase 1 is classifying correctly |
| Sources used | Shows which guidelines backed the answer — clinical reviewers need this to validate RAG quality |
| Phase 1/2 fallback flag | Red indicator when pipeline degraded — tells testers the answer quality may be lower |
| Risk tier | Always 0 until Risk Engine is built; included now so the panel structure is ready |

Debug panel is **collapsed by default** — testers who just want to chat don't see it; reviewers can expand it.

---

## 12. FastAPI App Structure — `api/app.py`

```python
@asynccontextmanager
async def lifespan(app):
    # STARTUP — runs once, blocks until done
    app.state.db_pool   = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=5)
    app.state.embedder  = load_embedder(settings.embedding_model)   # ~20s, D: drive
    app.state.reranker  = load_reranker(settings.reranker_model)    # ~10s, D: drive
    app.state.flash_client = build_flash_client()  # Gemini Flash for memory compression
    yield
    # SHUTDOWN
    await app.state.db_pool.close()

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
```

---

## 13. What This Does NOT Include (out of scope for this build)

| Item | When |
|------|------|
| Risk Engine (Tiers 1–4) | After base model validation — B4 blocker |
| Malayalam translation layer | After base model passes clinical sign-off |
| WhatsApp transport | After clinical sign-off — replaces session_id/user_id scheme |
| Lead capture + consent flow | After DB + engine are stable |
| Multi-worker / gunicorn deployment | When moving to cloud |
| HTTPS / SSL | When moving to cloud |

---

## 14. Build Order

1. `api/rate_limiter.py` — simplest, no deps
2. `api/audit_logger.py` — DB write, straightforward
3. `api/memory_compressor.py` — Gemini Flash call
4. `api/session_manager.py` — wires DB reads/writes around `handle_phase1()`
5. `api/routes/chat.py` — SSE streaming endpoint
6. `api/app.py` — lifespan, mount routes
7. `static/index.html` + `static/style.css` — chat UI shell
8. `static/app.js` — fetch + SSE parser + debug panel
9. End-to-end test: send a message, see streaming response, check audit log row
