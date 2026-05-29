#!/usr/bin/env python3
"""
tools/model_compare.py -- Multi-provider model comparison tool

Fetches all available chat-capable models from Groq and Cerebras (live, from
the API -- nothing hardcoded), sends the same prompt to every model, and saves
a side-by-side results file for the manager to review.

Rate limits
-----------
Groq free tier (per model, per minute): ~6 000 tokens/min, 14 400 req/day.
These are PER MODEL -- so querying 10 models sequentially hits 10 separate
buckets. The default --delay of 2s is enough headroom for the free tier.
If a 429 is returned the tool waits the retry-after time and retries once.

Cerebras limits are fetched live from the API once CEREBRAS_API_KEY is set.

Usage
-----
    python tools/model_compare.py --prompt "What is HbA1c?"
    python tools/model_compare.py --list-models
    python tools/model_compare.py --prompt "..." --providers groq
    python tools/model_compare.py --prompt "..." --providers cerebras
    python tools/model_compare.py --prompt "..." --providers groq,cerebras --delay 5
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import httpx

# Make stdout safe on Windows consoles that use cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDERS: dict = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "env_key":  "GROQ_API_KEY",
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "env_key":  "CEREBRAS_API_KEY",
    },
}

# Non-chat models to skip (ASR, TTS, tiny prompt-classifiers)
_SKIP_PATTERNS = ("whisper", "orpheus", "prompt-guard")
_MIN_CONTEXT   = 1024   # models with <1024 token context are classifiers, not chat


def _is_chat_model(m: dict) -> bool:
    mid = m["id"].lower()
    ctx = m.get("context_window")
    # Only exclude on context_window when the field is present; absent = provider doesn't report it
    if ctx is not None and ctx < _MIN_CONTEXT:
        return False
    return not any(pat in mid for pat in _SKIP_PATTERNS)


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------

def fetch_models(provider: str) -> list:
    """Return chat-capable models for a provider, fetched live from the API."""
    cfg     = PROVIDERS[provider]
    api_key = os.environ.get(cfg["env_key"], "").strip()
    if not api_key:
        print("  [skip] %s: %s not set in .env" % (provider, cfg["env_key"]))
        return []
    headers = {"Authorization": "Bearer %s" % api_key}
    try:
        resp = httpx.get(
            "%s/models" % cfg["base_url"],
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        return [m for m in resp.json()["data"] if _is_chat_model(m)]
    except Exception as e:
        print("  [error] %s: could not list models -- %s" % (provider, e))
        return []


# ---------------------------------------------------------------------------
# Single-model inference
# ---------------------------------------------------------------------------

def _supports_reasoning(model_id: str) -> bool:
    # openai/gpt-oss-* models on Groq accept reasoning_effort
    return "gpt-oss" in model_id.lower()


def run_model(provider: str, model_id: str, prompt: str) -> dict:
    """Call one model and return a result dict with timing + rate-limit info."""
    cfg     = PROVIDERS[provider]
    api_key = os.environ.get(cfg["env_key"], "").strip()

    body: dict = {
        "model":       model_id,
        "messages":    [{"role": "user", "content": prompt}],
        "temperature": 1,
        "max_tokens":  512,
    }
    if _supports_reasoning(model_id):
        body["reasoning_effort"] = "medium"

    headers = {
        "Authorization": "Bearer %s" % api_key,
        "Content-Type":  "application/json",
    }

    for attempt in range(2):   # one retry on 429
        t0 = time.time()
        try:
            resp    = httpx.post(
                "%s/chat/completions" % cfg["base_url"],
                headers=headers,
                json=body,
                timeout=120,
            )
            elapsed = round(time.time() - t0, 2)

            if resp.status_code == 429 and attempt == 0:
                wait = int(resp.headers.get("retry-after", 10))
                print("    rate-limited -- waiting %ds ..." % wait, flush=True)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data    = resp.json()
            choice  = data["choices"][0]
            usage   = data.get("usage", {})
            msg     = choice["message"]
            # zai-glm-4.7 and similar reasoning-only models return `reasoning` instead of `content`
            output  = msg.get("content") or msg.get("reasoning") or ""

            return {
                "provider":            provider,
                "model":               model_id,
                "output":              output,
                "finish_reason":       choice.get("finish_reason"),
                "input_tokens":        usage.get("prompt_tokens", 0),
                "output_tokens":       usage.get("completion_tokens", 0),
                "latency_s":           elapsed,
                "rl_limit_tokens_min": resp.headers.get("x-ratelimit-limit-tokens", "?"),
                "rl_remaining_tokens": resp.headers.get("x-ratelimit-remaining-tokens", "?"),
                "rl_reset_tokens":     resp.headers.get("x-ratelimit-reset-tokens", "?"),
                "error":               None,
            }

        except Exception as e:
            return {
                "provider":            provider,
                "model":               model_id,
                "output":              None,
                "finish_reason":       None,
                "input_tokens":        0,
                "output_tokens":       0,
                "latency_s":           round(time.time() - t0, 2),
                "rl_limit_tokens_min": "?",
                "rl_remaining_tokens": "?",
                "rl_reset_tokens":     "?",
                "error":               str(e),
            }

    return {"provider": provider, "model": model_id,
            "output": None, "error": "max retries exceeded"}


# ---------------------------------------------------------------------------
# Comparison runner
# ---------------------------------------------------------------------------

def run_comparison(prompt: str, providers: list, delay_s: float) -> list:
    results = []
    for provider in providers:
        print("\n" + "-" * 64)
        print("  " + provider.upper())
        print("-" * 64)
        models = fetch_models(provider)
        if not models:
            continue
        print("  %d chat-capable models\n" % len(models))
        for i, m in enumerate(models):
            mid = m["id"]
            print("  [%d/%d] %s ... " % (i + 1, len(models), mid), end="", flush=True)
            result = run_model(provider, mid, prompt)
            if result.get("error"):
                print("ERROR -- " + result["error"][:80])
            else:
                print(
                    "%ss | in=%d out=%d tokens | rate-limit=%s tok/min" % (
                        result["latency_s"],
                        result["input_tokens"],
                        result["output_tokens"],
                        result["rl_limit_tokens_min"],
                    )
                )
            results.append(result)
            if i < len(models) - 1:
                time.sleep(delay_s)
    return results


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def print_summary(results: list) -> None:
    print("\n" + "=" * 80)
    print("RESULTS")
    print("=" * 80 + "\n")
    for r in results:
        tag = "FAIL" if r.get("error") else "OK  "
        print("[%s]  [%s]  %s" % (tag, r["provider"], r["model"]))
        if r.get("error"):
            print("        error: " + r["error"])
        else:
            out     = (r.get("output") or "").replace("\n", " ").strip()
            preview = out[:400] + ("..." if len(out) > 400 else "")
            print("        latency=%ss  in=%d out=%d tok" % (
                r["latency_s"], r["input_tokens"], r["output_tokens"]))
            print("        %s" % preview)
        print()


def save_results(prompt: str, results: list) -> Path:
    logs_dir = Path(__file__).resolve().parent.parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = logs_dir / ("model_compare_%s.json" % ts)
    out_path.write_text(
        json.dumps({"timestamp": ts, "prompt": prompt, "results": results},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare all available chat models on Groq and Cerebras",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--prompt",      type=str,
                        help="Prompt to send to every model")
    parser.add_argument("--providers",   type=str,   default="groq,cerebras",
                        help="Comma-separated providers (default: groq,cerebras)")
    parser.add_argument("--delay",       type=float, default=2.0,
                        help="Seconds between model calls within a provider (default: 2)")
    parser.add_argument("--list-models", action="store_true",
                        help="List all available chat models and exit")
    args = parser.parse_args()

    requested = [p.strip().lower() for p in args.providers.split(",")]
    providers = [p for p in requested if p in PROVIDERS]
    unknown   = [p for p in requested if p not in PROVIDERS]
    if unknown:
        print("Unknown providers ignored: %s  (valid: %s)" % (unknown, list(PROVIDERS)))
    if not providers:
        print("No valid providers specified.")
        sys.exit(1)

    if args.list_models:
        for provider in providers:
            print("\n%s -- chat-capable models:" % provider.upper())
            models = fetch_models(provider)
            if not models:
                continue
            for m in models:
                print("  %-55s  ctx=%7s  max_out=%s" % (
                    m["id"],
                    m["context_window"] if "context_window" in m else "?",
                    m.get("max_completion_tokens", "?")))
        return

    if not args.prompt:
        parser.print_help()
        sys.exit(1)

    print("\nPrompt   : %r" % args.prompt)
    print("Providers: %s" % providers)
    print("Delay    : %ss between models" % args.delay)

    results  = run_comparison(args.prompt, providers, delay_s=args.delay)
    working  = [r for r in results if not r.get("error")]
    failed   = [r for r in results if r.get("error")]
    if failed:
        print("\nSkipped (not working): %s" % ", ".join(r["model"] for r in failed))
    print_summary(working)
    out_path = save_results(args.prompt, working)
    print("Saved to : %s" % out_path)


if __name__ == "__main__":
    main()
