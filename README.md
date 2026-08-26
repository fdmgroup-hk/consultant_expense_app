# Consultant Experience

An AI interview-preparation coach for FDM consultants heading into technology placements at investment banks.

It turns handover material written by returning consultants — PowerPoint decks, PDFs, Word write-ups — into a searchable knowledge base, then uses it for two things:

- **Ask** — grounded Q&A about financial concepts (trade lifecycle, order flow, settlement), what each technology role actually involves, and what previous consultants experienced on placement. Every answer cites the deck and slide it came from.
- **Practise** — role-specific mock interviews. One question at a time, an honest score out of 10, a model answer, and a follow-up that drills into whatever you left out.

The app ships with a starter knowledge pack, so it is useful before anyone uploads anything.

---

## Quickstart

**Windows (PowerShell)**

```powershell
cd C:\Users\Asus\FDM_APP
.\run.ps1 -Setup          # creates .venv, installs dependencies, creates .env
# add your free GOOGLE_API_KEY to .env, then:
.\run.ps1
```

**macOS / Linux**

```bash
./run.sh                  # first run sets everything up and creates .env
# add your free GOOGLE_API_KEY to .env, then:
./run.sh
```

**Manual, if you prefer**

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Windows
# .venv/bin/python -m pip install -r requirements.txt       # macOS/Linux
cp .env.example .env                                        # then edit it
.venv/Scripts/python -m uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000**. Interactive API docs are at `/docs`.

You need one thing to get started: a **free** Groq key from <https://console.groq.com/keys> (no card required). Put it in `.env` as `GROQ_API_KEY`. Everything else has a working default.

Prefer Claude? Set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` instead — both providers are supported and the switch touches only [app/llm.py](app/llm.py).

---

## Where the data lives

The app runs in two configurations from the same codebase, chosen by whether `DATABASE_URL` is set.

| | Local (default) | Hosted |
|---|---|---|
| Database | SQLite file, `data/consultant_experience.db` | Postgres (Supabase, Singapore) |
| Original decks | `data/uploads/` | Private S3 bucket (Supabase Storage) |
| Setup needed | none | see **[DEPLOY.md](DEPLOY.md)** |

**Local** needs no accounts and nothing leaves the machine except the questions and retrieved excerpts sent to the Claude API. Back it up by copying one file.

**Hosted** is Supabase + Render, both free tiers, region Singapore — the closest either offers to Hong Kong (~30–40ms). Neither has a Hong Kong region, and no free managed Postgres does; if residency is ever mandated, [DEPLOY.md](DEPLOY.md) covers the alternatives. Full walkthrough, free-tier limits and troubleshooting are in **[DEPLOY.md](DEPLOY.md)**.

### The trap this avoids

Free app hosts (Render, Fly, Railway) give you an **ephemeral filesystem**. Deploy the naive way and the app appears to work, then silently loses the entire knowledge base and every uploaded PowerPoint on the next deploy, restart or idle-timeout. That is why the hosted configuration puts the database in Postgres and the files in object storage, and why the app **refuses to start** if `STORAGE_BACKEND=s3` is misconfigured rather than quietly falling back to a disk that gets wiped.

### Who can read it

As shipped, **anyone with the URL can read and search the knowledge base**; uploading, deleting and downloading original files require `ADMIN_TOKEN`.

That is fine while the app holds only the starter pack. It stops being fine the moment a real client handover deck goes in, because a Render URL is guessable and not secret. Setting one environment variable closes it:

```
APP_PASSWORD=<something long>
```

The whole site then sits behind a browser login prompt — pages, API and static files alike, with `/healthz` left open for the platform's health probe. No code change.

Worth saying plainly once: decks describing a named bank's systems, incidents and processes are commercially sensitive and quite possibly covered by the client contract. Free tiers are appropriate for the starter pack and generic material. For real HSBC handover decks, set `APP_PASSWORD` at minimum, and consider hosting somewhere FDM controls with SSO in front.

### Moving an existing local knowledge base up

```bash
python -m scripts.migrate_to_postgres --dry-run
python -m scripts.migrate_to_postgres --with-originals --with-history
```

Safe to re-run — documents already in the target are matched on content hash and skipped.

---

## How it works

```
Upload (.pptx/.pdf/.docx/.md/.txt)
        │
        ▼
  Extract text ───────── python-pptx keeps slide numbers AND speaker notes
        │                pypdf keeps page numbers
        ▼
  Chunk ──────────────── ~1100 chars, small slides packed together,
        │                long sections split with overlap
        ▼
  Embed ──────────────── Voyage / fastembed / built-in hashing
        │
        ▼
  Store ──────────────── SQLite locally / Postgres hosted
        │                (originals to disk or an S3 bucket)
        │
        ▼
  Retrieve ───────────── BM25 keyword  +  vector similarity
        │                fused with Reciprocal Rank Fusion
        ▼
  Claude Opus 5 ──────── streamed answer with inline [1][2] citations
```

**Why hybrid retrieval.** BM25 alone nails the jargon (`T+2`, `FIX 35=D`, `PnL break`) but misses paraphrases. Vectors alone catch "what happens after a trade is agreed" but drift on exact identifiers. Fusing the two rankings handles both, and it means the app still retrieves sensibly when no embedding model is installed.

**Citations are structural, not decorative.** Retrieved chunks are numbered in the prompt and carry their source deck, slide number, client and role. The system prompt requires the model to separate what came from the decks from its own background knowledge, so a consultant can tell which parts are lived experience from a previous placement.

### Layout

```
app/
  main.py              FastAPI app, startup, /api/status
  config.py            settings from .env
  db.py                schema + connections; speaks SQLite or Postgres
  storage.py           original files: local disk, S3-compatible, or none
  llm.py               Claude client: streaming + schema-validated JSON
  prompts.py           system prompts, role briefs, output schemas
  security.py          admin-token gate for writes
  ingest/
    extract.py         pptx / pdf / docx / md / txt -> text with locators
    chunk.py           segments -> retrieval-sized chunks
    pipeline.py        extract -> chunk -> embed -> store
    seed.py            loads the starter pack on first run
  retrieval/
    embeddings.py      Voyage / fastembed / hashing, auto-selected
    search.py          BM25 + vector + RRF
  routers/
    documents.py       upload, list, search, delete, re-index
    chat.py            SSE streaming chat
    interview.py       mock interview: start / answer / next / review
web/                   the UI - plain HTML, CSS and ES modules, no build step
seed/                  starter knowledge pack (markdown)
scripts/
  bulk_ingest.py       index a whole folder at once
  migrate_to_postgres.py   move a local knowledge base to the hosted one
tests/test_smoke.py    runs without an API key, against SQLite or Postgres

Dockerfile             production image
render.yaml            Render blueprint - creates the service from this repo
DEPLOY.md              step-by-step Supabase + Render setup
```

---

## Adding consultant material

**Through the UI:** the *Knowledge base* tab. Pick a file, tag it with the client, role and placement period, enter the admin token, upload. Tagging matters — it is what lets someone filter to "Production Support at HSBC" and what makes mock-interview questions role-appropriate.

**In bulk:**

```bash
.venv/Scripts/python -m scripts.bulk_ingest "C:\handovers\HSBC 2025" --client HSBC --role developer

# or let the folder structure supply the metadata:
#   <root>/HSBC/production_support/2025H1/deck.pptx
.venv/Scripts/python -m scripts.bulk_ingest "C:\handovers" --infer-from-path

.venv/Scripts/python -m scripts.bulk_ingest "C:\handovers" --dry-run   # preview first
```

Supported: `.pptx`, `.pdf`, `.docx`, `.md`, `.txt`, up to 60 MB each.

**Notes on extraction quality.** PowerPoint speaker notes are indexed along with the slide body — they are often where a consultant explains what a slide actually means, and they make the knowledge base noticeably better. Scanned PDFs and image-only slides contain no extractable text and will be rejected; they need OCR first. Legacy `.ppt` files must be re-saved as `.pptx`.

**Anonymising.** The consultant name field is free text and optional. Initials or "2025 cohort" work fine, and the app never requires a real name.

---

## Configuration

Everything lives in `.env` (see `.env.example`).

| Setting | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `groq` | `groq` (free), `gemini` (free, tight quota) or `anthropic` (paid) |
| `GROQ_API_KEY` | — | Free key from console.groq.com; required for chat and practice |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Open-weight reasoning model on Groq |
| `GOOGLE_API_KEY` | — | Only when `LLM_PROVIDER=gemini` |
| `GEMINI_MODEL` | `gemini-flash-lite-latest` | Covered by the free tier |
| `ANTHROPIC_API_KEY` | — | Only when `LLM_PROVIDER=anthropic` |
| `ANTHROPIC_MODEL` | `claude-opus-5` | `claude-sonnet-5` is cheaper and still strong here |
| `ANTHROPIC_EFFORT` | `high` | `medium` or `low` cuts latency and cost noticeably |
| `ANTHROPIC_THINKING_DISPLAY` | `summarized` | Shows a reasoning summary in the UI; `omitted` hides it |
| `ANTHROPIC_SERVER_FALLBACKS` | `true` | Server-side refusal fallback. Set `false` on Bedrock/Vertex/Foundry |
| `EMBEDDING_PROVIDER` | `auto` | `voyage`, `fastembed`, `hashing`, or `auto` |
| `ADMIN_TOKEN` | `change-me-…` | Required to upload or delete. **Change it.** |
| `APP_PASSWORD` | *(empty)* | Set it to put the whole site behind a login prompt |
| `DATABASE_URL` | *(empty)* | Empty = SQLite. A `postgresql://` URL = hosted Postgres |
| `STORAGE_BACKEND` | `local` | `local`, `s3` or `none`. Must be `s3` when hosted |
| `RETRIEVAL_TOP_K` | `8` | Excerpts passed to the model per question |
| `CHUNK_TARGET_CHARS` | `1100` | Bigger = more context per excerpt, fewer excerpts |
| `DATA_DIR` | `./data` | Where the database and uploads live |

Generate an admin token with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Choosing an embedding provider

The app runs out of the box with **`hashing`** — deterministic feature hashing in pure numpy, no install, no network. It is weaker on synonyms than a real model, which is exactly why retrieval always fuses it with BM25. Fine for a pilot.

For better retrieval, pick one:

```bash
# Local — nothing leaves the machine, no API key, ~90 MB model downloaded once
pip install fastembed

# Hosted — best quality, generous free tier
pip install voyageai        # then set VOYAGE_API_KEY in .env
```

Then set `EMBEDDING_PROVIDER` and **re-index** — the *Re-index* button on the Knowledge base tab, or `POST /api/documents/reindex`. Vectors from a different model are ignored by search until they are regenerated, so skipping this step silently degrades retrieval to keyword-only.

---

## What it costs

**On the default setup: nothing.** Gemini's free tier covers the model calls, Supabase's free tier covers the database and file storage, and Render's free tier covers hosting. The embedding step runs locally and needs no key at all.

The free tier has rate limits rather than charges — a burst of simultaneous users can hit "rate limit, wait a minute" rather than a bill. Verify the current limits when you create the key.

If you switch to `LLM_PROVIDER=anthropic`, `claude-opus-5` costs $5/$25 per million input/output tokens — roughly **5–6 US cents per question**, so a 20-question practice session is about $1–2. Better at nuanced interview scoring; the trade-off is real but so is the cost.

---

## Tests

```bash
.venv/Scripts/python -m pip install pytest httpx
.venv/Scripts/python -m pytest -q
```

They cover chunking, ingestion, retrieval, role filtering, storage and the HTTP API, and run against a throwaway database with the `hashing` embedder — **no API key needed**.

To run them against Postgres instead of SQLite, point `DATABASE_URL` at any Postgres and re-run:

```bash
docker run -d --name pg -e POSTGRES_PASSWORD=dev -p 5432:5432 postgres:16-alpine
DATABASE_URL=postgresql://postgres:dev@127.0.0.1:5432/postgres .venv/Scripts/python -m pytest -q
```

---

## API

Full interactive docs at `/docs` when the app is running.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/status` | Counts, embedding provider, whether the key is set |
| `GET` | `/api/documents` | List indexed material (filter by `role`, `client`) |
| `POST` | `/api/documents` | Upload and index (needs `X-Admin-Token`) |
| `DELETE` | `/api/documents/{id}` | Remove a document and its chunks |
| `GET` | `/api/documents/{id}/chunks` | Inspect exactly what was indexed |
| `GET` | `/api/documents/{id}/original` | Download the deck as uploaded (admin only) |
| `GET` | `/api/documents/search/query?q=` | Hybrid search, no LLM involved |
| `POST` | `/api/documents/reindex` | Re-embed everything with the current model |
| `POST` | `/api/chat/stream` | Streaming grounded chat (SSE) |
| `GET` | `/api/chat/sessions` | Conversation history |
| `POST` | `/api/interview/start` | Begin a mock interview |
| `POST` | `/api/interview/answer` | Submit an answer, get a scored assessment |
| `POST` | `/api/interview/next` | Next question, or the follow-up |
| `GET` | `/api/interview/sessions/{id}` | Full transcript and average score |
| `GET` | `/healthz` | Liveness probe; touches the database |

---

## Deliberately not built

Worth knowing before anyone asks for a demo:

- **No user accounts.** `APP_PASSWORD` gives you one shared site password and `ADMIN_TOKEN` protects writes, but there is no per-person identity, no audit of who read what, and no way to revoke one individual. Supabase Auth is the natural next step and is not built.
- **No OCR.** Scanned PDFs and image-only slides are rejected rather than silently indexed as empty.
- **No automatic PII redaction.** Whoever uploads is responsible for what is in the deck. A reasonable next step is a review queue between upload and indexing.
- **No usage analytics.** Nothing tracks which consultants practised or how they scored beyond what is stored locally in their own session history.
- **Retrieval is in-memory.** Both halves of search load into the process at boot. Fine to roughly 50k chunks — a few hundred decks, comfortably inside the Supabase free tier. Past that, move the vector half into `pgvector`; [DEPLOY.md](DEPLOY.md) says exactly what to change.
- **No automated backups.** Neither free tier backs anything up. `pg_dump` on a schedule once the knowledge base is worth keeping.
