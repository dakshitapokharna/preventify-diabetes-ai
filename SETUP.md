# Developer Setup

## Why: C Drive Is Almost Full

The system C: drive has less than 1 GB free. Installing Python packages and
downloading ML models to the default location (C:\Users\...) will fail or
cause problems. All Python dependencies and model weights must live on D: drive.

---

## Step 1 — Create a Virtual Environment on D: Drive

Open PowerShell in the project folder and run:

```powershell
# Create the venv inside the project folder (D: drive)
python -m venv D:\dakshita\exlaty\.venv

# Activate it
D:\dakshita\exlaty\.venv\Scripts\Activate.ps1
```

After activation your prompt will show `(.venv)`.

**Why inside the project folder:**
- All `pip install` downloads go to `D:\dakshita\exlaty\.venv\Lib\site-packages\` — not C:
- The venv is self-contained; delete the folder to fully clean up
- `.venv` is already in `.gitignore` — it will never be committed

---

## Step 2 — Install Dependencies

With the venv active:

```powershell
pip install -r requirements.txt
```

This installs everything listed in `requirements.txt` into the D: drive venv.

---

## Step 3 — Redirect HuggingFace Model Cache to D: Drive

The embedding model (`BAAI/bge-large-en-v1.5`, ~1.3 GB) and reranker
(`BAAI/bge-reranker-large`, ~1.1 GB) download to HuggingFace's cache folder.
By default that is `C:\Users\<you>\.cache\huggingface` — which will fail on a
full C: drive.

Set these two environment variables **before running any embedder or reranker script:**

```powershell
$env:HF_HOME = "D:\hf_cache"
$env:TRANSFORMERS_CACHE = "D:\hf_cache"
```

Or add them permanently to your PowerShell profile
(`$PROFILE` — usually `Documents\PowerShell\Microsoft.PowerShell_profile.ps1`):

```powershell
$env:HF_HOME = "D:\hf_cache"
$env:TRANSFORMERS_CACHE = "D:\hf_cache"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
```

The last line suppresses a Windows symlink warning that appears when Developer
Mode is not enabled.

---

## Step 4 — Copy .env

```powershell
copy .env.example .env
```

Then open `.env` and fill in:
- `POSTGRES_URL` — your Neon connection string
- `LLM_API_KEY` — your Anthropic API key

`.env` is gitignored. Never commit it.

---

## Running the Embedder

```powershell
# Activate venv first
D:\dakshita\exlaty\.venv\Scripts\Activate.ps1

# Set HF cache
$env:HF_HOME = "D:\hf_cache"
$env:TRANSFORMERS_CACHE = "D:\hf_cache"

# Dry run — no DB writes, just count chunks
python -m ingestion.embedder.run --dry-run

# Full run — embed all 1,945 chunks and upsert to Neon
python -m ingestion.embedder.run

# Single source only
python -m ingestion.embedder.run RSSDI_2022

# Retry any failed chunks
python -m ingestion.embedder.run --retry-failed
```

---

## Disk Space Reference

| Location | Drive | Size |
|----------|-------|------|
| Python venv + packages | D: (project folder) | ~2 GB |
| bge-large-en-v1.5 model | D:\hf_cache | ~1.3 GB |
| bge-reranker-large model | D:\hf_cache | ~1.1 GB |
| Corpus PDFs | D: (corpus/) | ~500 MB |
| Chunk JSONL files | D: (data/chunks/) | ~10 MB |
| **Total on D:** | | **~5 GB** |

C: drive is not used for any project storage after following this setup.
