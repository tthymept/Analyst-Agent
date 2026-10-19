# Analyst Agent

A personal data platform: my expense tracker (Google Sheets) → Databricks (bronze / silver / gold) → dashboard → an LLM data-analyst agent that answers questions in plain English.

Status: **Phase 0**: ingestion and deployment plumbing.

## How data flows

1. **Ingest** (`.github/workflows/ingest.yml`, daily 02:00 SGT) runs `uv run ingest-sheet`
   (`src/analyst_agent/ingest/pull_sheet.py`): reads every tab of the sheet with a read-only
   Google service account and uploads one raw JSON snapshot to a Unity Catalog Volume.
2. **Bronze** (`notebooks/bronze/load_snapshots.py`, job `bronze_load`, serverless) appends new
   snapshots to `bronze.sheet_rows`, one row per sheet row, untouched. Idempotent per snapshot.
3. **Silver / gold**: next phases.

**Deploy** (`.github/workflows/deploy.yml`) runs `databricks bundle deploy` on every push to `main`.

## Layout

```
databricks.yml              Databricks Asset Bundle config
resources/                  jobs and pipelines (YAML)
notebooks/                  code that runs on Databricks
src/analyst_agent/          Python package: ingest now, the agent later
setup/                      one-time SQL
.github/workflows/          deploy + daily ingest
```

## Run locally

```bash
uv sync                              # install dependencies from uv.lock
databricks bundle validate
databricks bundle deploy
databricks bundle run bronze_load
uv run ingest-sheet                  # needs a .env, see .env.example
```

## Secrets and data

No data and no credentials live in this repo. GitHub Secrets: `DATABRICKS_HOST`, `DATABRICKS_TOKEN`,
`GOOGLE_SA_JSON`, `SHEET_ID`. Locally: `.env` (gitignored) and `~/.databrickscfg`.
