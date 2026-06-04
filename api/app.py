"""
api/app.py — FastAPI application entry point

Startup sequence (runs once, blocking):
  1. Create asyncpg connection pool → app.state.db_pool
  2. Load bge-large-en-v1.5 embedder → app.state.embedder   (~20s, D: drive)
  3. Load bge-reranker-v2-m3 → app.state.reranker             (~10s, D: drive)

Shutdown:
  Close the asyncpg pool.

Serve:
  POST /chat — SSE streaming endpoint (routes/chat.py)
  GET  /health — liveness check
  GET  /       — serves static/index.html
  static/      — CSS + JS

Usage:
    uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload
"""

import logging
import os
from contextlib import asynccontextmanager

# Load .env into os.environ BEFORE anything reads env vars.
# pydantic_settings populates Settings() but does NOT set os.environ —
# runners (phase1_runner, phase2_runner) read os.environ directly.
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

import asyncpg
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config.settings import settings
from engine.phase1 import load_ml_models
from api.routes.chat import router as chat_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Lifespan — startup + shutdown
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── STARTUP ───────────────────────────────────────────────────────────────
    log.info("startup: connecting to Neon PostgreSQL...")
    from pgvector.asyncpg import register_vector

    async def _init_conn(conn):
        """Register pgvector type on every new pool connection."""
        await register_vector(conn)

    app.state.db_pool = await asyncpg.create_pool(
        settings.postgres_url,
        min_size=2,
        max_size=5,
        command_timeout=30,
        init=_init_conn,
    )
    log.info("startup: DB pool ready (pgvector registered)")

    log.info("startup: loading ML models (this takes ~30s)...")
    embedder, reranker = load_ml_models(settings)
    app.state.embedder = embedder
    app.state.reranker = reranker
    log.info("startup: ML models loaded — ready to serve")

    yield

    # ── SHUTDOWN ──────────────────────────────────────────────────────────────
    log.info("shutdown: closing DB pool")
    await app.state.db_pool.close()
    log.info("shutdown: complete")


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Preventify Diabetes Educator AI",
    description="Malayalam-first diabetes education chatbot — testing interface",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,   # disable Swagger UI in this phase
    redoc_url=None,
)

# ── Static files ───────────────────────────────────────────────────────────
import pathlib
STATIC_DIR = pathlib.Path(__file__).resolve().parent.parent / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── Routes ─────────────────────────────────────────────────────────────────
app.include_router(chat_router)


@app.get("/")
async def index():
    """Serve the chat frontend."""
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return FileResponse(str(html_path))
    return JSONResponse({"error": "static/index.html not found"}, status_code=404)


@app.get("/health")
async def health():
    """Liveness check — confirms DB + models are available."""
    db_ok = False
    try:
        async with app.state.db_pool.acquire() as conn:
            await conn.execute("SELECT 1")
        db_ok = True
    except Exception as exc:
        log.warning("health: DB check failed — %s", exc)

    models_ok = (
        hasattr(app.state, "embedder") and app.state.embedder is not None
        and hasattr(app.state, "reranker") and app.state.reranker is not None
    )

    status = "ok" if (db_ok and models_ok) else "degraded"
    return {
        "status": status,
        "db":     "connected" if db_ok else "error",
        "models": "loaded"    if models_ok else "not loaded",
    }
