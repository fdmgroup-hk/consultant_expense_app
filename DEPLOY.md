# Deploying Consultant Experience

Target stack: **Supabase** (Postgres + object storage, Singapore) and **Render** (the app, Singapore). Both on free tiers. Budget about 30 minutes.

Singapore is the closest region either provider offers to Hong Kong — roughly 30–40ms, imperceptible in a chat app. Neither has a Hong Kong region, and no free managed Postgres does. If Hong Kong residency is ever mandated, see [Moving off the free tier](#moving-off-the-free-tier).

---

## Before you start

You need three accounts, all free: [Supabase](https://supabase.com), [Render](https://render.com), and a free [Google Gemini API key](https://aistudio.google.com/apikey) (no card required). On this setup every tier is free — see the cost section in [README.md](README.md).

The code must be on GitHub for Render to build it.

---

## Step 1 — Supabase: database

1. **New project.** Name it `consultant-experience`. Choose region **Southeast Asia (Singapore)**. Set a strong database password and save it somewhere — you will need it in step 3, and Supabase will not show it again.
2. Wait for provisioning (~2 minutes).
3. Go to **Project Settings → Database → Connection string → Transaction pooler** and copy the URI. It looks like:

   ```
   postgresql://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres
   ```

   Replace `[YOUR-PASSWORD]` with the password from step 1.

**Use the transaction pooler (port 6543), not the direct connection (5432).** The free tier allows few direct connections, and the pooler is what keeps the app from exhausting them.

The app creates its own tables on first boot. There is no migration step to run.

## Step 2 — Supabase: storage for the original decks

1. **Storage → New bucket.** Name it `consultant-originals`.
2. Leave **Public bucket OFF.** This is the setting that matters most on this page — a public bucket would make every uploaded PowerPoint downloadable by anyone with the URL, bypassing the app entirely.
3. Go to **Project Settings → API** and copy two values: the **Project URL** (`https://<ref>.supabase.co`) and the **`service_role`** key.

No S3 access keys are needed — `STORAGE_BACKEND=supabase` talks to Storage over its REST API using the service-role key. (The `s3` backend is still there if you ever move to Cloudflare R2 or MinIO.)

**The `service_role` key bypasses row-level security.** It belongs only in Render's environment variables — never in the repo, a ticket, or a chat.

## Step 3 — Render: the app

1. Push this repository to GitHub.
2. In Render: **New → Blueprint**, connect the repo. Render reads [render.yaml](render.yaml), sees the Dockerfile and configures the service.
3. Render prompts for the values marked `sync: false`. Fill in:

   | Variable | Value |
   |---|---|
   | `GOOGLE_API_KEY` | free key from <https://aistudio.google.com/apikey> |
   | `DATABASE_URL` | the pooler URI from step 1 |
   | `ADMIN_TOKEN` | generate one — see below |
   | `SUPABASE_SERVICE_KEY` | the `service_role` key from step 2 |
   | `APP_PASSWORD` | optional — see [Who can get in](#who-can-get-in) |

   Generate the admin token with:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

4. Deploy. First build takes 5–10 minutes. Watch the logs for:

   ```
   Database: Postgres
   Originals: s3 bucket 'consultant-originals'
   Loaded 7 starter knowledge documents.
   Application startup complete.
   ```

5. Open the URL Render gives you. The starter pack is already searchable; ask it something.

6. **Verify it properly.** The browser cannot show you whether `DATABASE_URL` actually landed — an app running on SQLite looks perfectly healthy right up until the next deploy wipes it. Run:

   ```bash
   python -m scripts.verify_deployment https://your-app.onrender.com --admin-token <your token>
   ```

   It checks the database is Postgres, storage is S3, the admin token is not the default, the Claude key is set, retrieval returns hits and the UI is served. Anything reported as FAIL should be fixed before real material goes in.

**If the logs say `Database: SQLite`,** `DATABASE_URL` did not reach the app. Everything will appear to work and then lose all data on the next deploy. Fix it before uploading anything.

## Step 4 — bring your local knowledge base up (optional)

If you already indexed decks locally:

```bash
# Windows
set DATABASE_URL=postgresql://postgres.abc:pass@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres
set STORAGE_BACKEND=s3
set S3_ENDPOINT_URL=https://abcdefgh.supabase.co/storage/v1/s3
set S3_BUCKET=consultant-originals
set S3_ACCESS_KEY_ID=...
set S3_SECRET_ACCESS_KEY=...

python -m scripts.migrate_to_postgres --dry-run
python -m scripts.migrate_to_postgres --with-originals
```

It skips anything already present, so it is safe to re-run.

---

## Who can get in

As configured, **anyone with the URL can read and search the knowledge base.** Uploading, deleting and downloading original files require `ADMIN_TOKEN`.

That is a reasonable setting while the app only holds the starter pack. It stops being reasonable the moment someone uploads a real client handover deck, because a Render URL is guessable and not secret.

To close it, set one environment variable in Render:

```
APP_PASSWORD=<something long>
```

The whole site then sits behind a browser login prompt — API, pages and static files alike. `/healthz` stays open so Render's health check keeps working. No code change, no redeploy beyond the env var save.

For per-person accounts rather than one shared password, Supabase Auth is the natural next step, since you are already on Supabase. That is a larger change and is not built.

---

## What "free" actually means here

| | Free allowance | What happens at the limit |
|---|---|---|
| Supabase database | 500 MB | Roughly 50,000 chunks — several hundred decks. Writes are refused past it. |
| Supabase storage | 1 GB | About 100–300 PowerPoints. |
| Supabase project | — | **Pauses after 7 days with no activity.** One click in the dashboard restores it. |
| Render web service | 750 hours/month | Enough for one always-listed service. |
| Render free instance | — | **Spins down after 15 minutes idle.** Next visitor waits ~50 seconds for a cold start. |
| Anthropic API | none — pay per use | ~5–6 US cents per question. See [README.md](README.md). |

Two consequences worth planning around:

- **The 7-day Supabase pause is the real trap.** A tool used in bursts before placement intakes can sit idle for weeks. Someone should open it fortnightly, or the first person back finds it down.
- **The 50-second cold start** looks like the app is broken to a first-time user. If that matters, Render's Starter plan (~$7/month) removes it.

Neither the database nor the file storage is backed up on the free tiers. Supabase adds daily backups on Pro. Until then, `pg_dump` on a schedule is worth setting up if the knowledge base becomes valuable.

---

## Running the container locally

Worth doing once before you deploy, to confirm the image works:

```bash
docker build -t consultant-experience .
docker run -p 8000:8000 --env-file .env consultant-experience
```

With no `DATABASE_URL` in `.env` it runs on SQLite inside the container — fine for a smoke test, but that data disappears with the container.

---

## Moving off the free tier

The tripwires, in the order you are likely to hit them:

- **Supabase pausing annoys people** → Supabase Pro, $25/month, which also brings daily backups.
- **Cold starts annoy people** → Render Starter, ~$7/month.
- **Hong Kong data residency gets mandated** → free managed Postgres cannot do this. Run Postgres yourself on a Fly.io volume in `hkg`, or use Azure Database for PostgreSQL in East Asia, or an FDM-hosted server. The app needs no code change — only `DATABASE_URL`.
- **Retrieval slows past ~50,000 chunks** → the vector half of search currently loads into memory. Move it into `pgvector`: enable the extension, change `embeddings.vector` from `BYTEA` to `vector`, and replace the numpy dot product in [app/retrieval/search.py](app/retrieval/search.py) with an `ORDER BY vector <=> query` clause. The BM25 half can stay as it is.

---

## Troubleshooting

**`Database: SQLite` in the logs on Render** — `DATABASE_URL` is missing or not a `postgresql://` URL. Check for a stray quote in the Render dashboard.

**`Object storage is misconfigured`** — the app refuses to start rather than silently falling back to a disk that gets wiped. Check `S3_ENDPOINT_URL`, `S3_BUCKET` and the two keys. The endpoint must end in `/storage/v1/s3`.

**`too many connections`** — you are on the direct connection string. Switch to the transaction pooler on port 6543.

**Uploads work, then files vanish after a deploy** — `STORAGE_BACKEND` is `local`. Set it to `s3`.

**Chat returns 503** — `GOOGLE_API_KEY` is not set. Browsing and search work without it; chat and interview practice do not.

**Everything is slow on the first request of the day** — Render free spun the instance down. Expected; see the table above.
