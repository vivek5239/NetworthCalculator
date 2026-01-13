# Copilot / AI Agent Instructions for FinanceApp Clone

Purpose: brief, actionable guidance so an AI coding agent can be productive immediately.

**Big Picture**
- **UI:** `app.py` is a Streamlit frontend (Net Worth Tracker) that reads/writes the local SQLite DB and provides import UI (see [app.py](app.py#L1-L40)).
- **API:** `api.py` is a small FastAPI service exposing `/assets` and `/history` and uses the same SQLite DB as the UI (see [api.py](api.py#L1-L40)).
- **Background processing:** `background_updater.py` runs periodic (30m) price updates, AI ticker fixes (Groq), and Gotify alerts; `scheduler.py` runs `daily_email_report.py` once a day when enabled (see [background_updater.py](background_updater.py#L1-L40) and [scheduler.py](scheduler.py#L1-L40)).
- **DB:** default DB file is `finance.db` in project root. All scripts honour `DB_FILE_PATH` environment variable. Model classes (`Asset`, `AppSettings`, `PortfolioHistory`) are defined repeatedly across files — edit consistently.

**Quick Start / Useful Commands**
- Run the Streamlit app (Windows dev environment): use the existing launcher: [start_app_8502.bat](start_app_8502.bat#L1-L3).
- Run the API locally: [start_api.bat](start_api.bat#L1-L3) (starts `uvicorn api:app --reload`).
- Run the daily report manually: [run_daily_report.bat](run_daily_report.bat#L1-L3).
- Override DB path for containers or CI: set `DB_FILE_PATH` to an absolute path before launching any script.

**Project-specific patterns & gotchas**
- Multiple files declare SQLAlchemy models independently. When changing the schema: update `app.py`, `api.py`, `daily_email_report.py`, `background_updater.py`, and `scheduler.py` together. `app.py:init_db()` contains ad-hoc ALTER TABLE migration attempts — prefer small, backwards-compatible changes.
- `AppSettings` uses a single row (id=1) as the global config; many scripts expect that. Do not assume multiple settings rows.
- Scripts use `BASE_DIR` (absolute file path) to locate the DB and to run subprocesses; relative-path-only changes can break scheduled runs (cron / Docker containers).
- Price ingestion: `yfinance` history is the primary source; code frequently falls back to `ticker.info`. Expect flaky behaviour; tests or error handling should inspect both code paths (`update_prices_from_yfinance` in [app.py](app.py#L200-L260), headless update in [daily_email_report.py](daily_email_report.py#L1-L60)).
- AI integrations live in `background_updater.py` (Groq) and `daily_email_report.py` (content for notifications). Keep prompt edits minimal and respect token limits; `MAX_AI_CORRECTIONS` exists to prevent excessive calls.

**Integration points & external dependencies**
- yfinance (live prices).
- Groq client (AI completions) — API key stored in DB (`AppSettings.groq_api_key`) or `GROQ_API_KEY` env var.
- Gotify + SMTP — notification channels configured via `AppSettings`.
- Docker: there is a `Dockerfile` and `docker-compose.yml` in the repo; confirm `DB_FILE_PATH` semantics when containerising.

**When editing code**
- If you change table columns: add safe ALTER TABLE logic (as in `app.py:init_db`) or provide a migration path; do not only change one declaration.
- If you change how the DB path is resolved, update all batch files and scheduled scripts that call Python directly (see [start_app_8502.bat](start_app_8502.bat#L1-L3), [start_api.bat](start_api.bat#L1-L3)).
- For AI prompt or model swaps, edit prompts inside `background_updater.py` and `daily_email_report.py` and preserve the concise result formatting expected by `send_gotify_alert` and `send_gotify`.

**Files to inspect first (quick links)**
- [app.py](app.py#L1-L40) — Streamlit UI, DB init and migrations
- [api.py](api.py#L1-L40) — FastAPI endpoints and DB models
- [background_updater.py](background_updater.py#L1-L40) — 30-min updater, AI integration
- [daily_email_report.py](daily_email_report.py#L1-L40) — headless price updates + email/Gotify report
- [scheduler.py](scheduler.py#L1-L40) — daily scheduler calling `daily_email_report.py`
- [start_app_8502.bat](start_app_8502.bat#L1-L3), [start_api.bat](start_api.bat#L1-L3), [run_daily_report.bat](run_daily_report.bat#L1-L3) — local dev entrypoints

If anything above is unclear or you'd like me to expand a section (DB migrations, Docker usage, or AI prompt guidelines), tell me which area to refine.
