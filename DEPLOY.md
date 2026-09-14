# Deploy the operations desk

The dashboard is a FastAPI app (`run_dashboard.py`) that reads and writes the same Supabase tables as the local pipeline. Keep the scraper on a schedule; do not start Apify from the web process.

## 1. Local schedule (this machine)

One cycle now:

```powershell
.\.venv\Scripts\python.exe run_scheduled_pipeline.py --mock
```

Daily at 06:00 via Windows Task Scheduler:

```powershell
.\schedule_pipeline.ps1 -Register -Mock
```

Remove the task with `.\schedule_pipeline.ps1 -Unregister`.

Linux/macOS cron (06:00 every day):

```cron
0 6 * * * cd "/path/to/Meridian Flow Network" && .venv/bin/python run_scheduled_pipeline.py --mock >> logs/pipeline.log 2>&1
```

Useful flags and env vars:

| Purpose | Flag | Environment |
| --- | --- | --- |
| No LLM credits | `--mock` | `PIPELINE_MOCK=1` or `LLM_PROVIDER=mock` |
| Skip Apify | `--skip-scrape` | `PIPELINE_SKIP_SCRAPE=1` |
| Local sample buyers | `--fixture` | `PIPELINE_FIXTURE=1` |
| Repeat as a worker | `--interval-hours 12` | `PIPELINE_INTERVAL_HOURS=12` |

Logs: `logs/pipeline.log`. A lockfile at `logs/pipeline.lock` stops overlapping runs.

## 2. Cloud host — Render

1. Push this repo to GitHub (do not commit `.env`).
2. In Render, **New → Blueprint** and point at `render.yaml`, or create two services manually:
   - **Web**: build `pip install -r requirements.txt`, start `python run_dashboard.py`.
   - **Cron**: `0 6 * * *` → `python run_scheduled_pipeline.py`.
3. Set environment variables on both services:

   - `SUPABASE_URL`
   - `SUPABASE_KEY`
   - `DASHBOARD_USER` / `DASHBOARD_PASSWORD` (web only — required on a public URL)
   - `APIFY_TOKEN` (cron only, unless you stay on `--fixture`)
   - `LLM_PROVIDER=mock` until OpenAI/Anthropic credits exist
   - `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` when you switch off mock
4. Open the Render URL. The browser will ask for the dashboard password.

## 3. Cloud host — Railway

1. Push the repo and **New Project → Deploy from GitHub**.
2. Railway reads `railway.toml` and starts `python run_dashboard.py`. It injects `PORT`; the app binds `0.0.0.0` automatically.
3. Add the same environment variables as Render, including `DASHBOARD_PASSWORD`.
4. Add a second service (or a Railway cron) with start command:

   ```text
   python run_scheduled_pipeline.py
   ```

   Worker loop instead of cron:

   ```text
   python run_scheduled_pipeline.py --interval-hours 12
   ```

## 4. What not to deploy

- Do not expose `SUPABASE_SERVICE_ROLE_KEY` in the browser. The desk only needs the Data API key already used locally.
- Do not run `setup_schema.py` from the public web process. Schema stays a one-time local/SQL Editor step.
- Leave `DASHBOARD_PASSWORD` empty only on `127.0.0.1`.

Health check for both hosts: `GET /health`.

## 5. GitHub Actions (scheduled scrape + match)

The workflow at `.github/workflows/pipeline.yml` runs daily at 06:00 UTC and can be started by hand from the Actions tab.

Add these **repository secrets** (Settings → Secrets and variables → Actions). Values are never printed in logs.

| Secret | Required | Used for |
| --- | --- | --- |
| `SUPABASE_URL` | yes | Data API |
| `SUPABASE_KEY` | yes | Data API |
| `APIFY_TOKEN` | yes on scheduled live scrapes | Residential crawl |
| `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` | for live LLM | Enrich + outreach |
| `LLM_PROVIDER` | optional | `openai`, `anthropic`, or `mock` |
| `PIPELINE_MOCK` | optional | Set `1` to force mock on the daily run |
| `MATCH_SCORE_THRESHOLD` | optional | Default 60 |
| `APIFY_ACTOR_ID` / `LLM_MODEL` | optional | Overrides |

Scheduled runs use `--mock` when no LLM key is present, or when `LLM_PROVIDER=mock` / `PIPELINE_MOCK=1`. Manual runs default to mock so you can test without credits.

Do not put `.env` in the repo. The workflow reads GitHub secrets only.
