# Phase 0 build log

What each step set up, how to check it, and its commit. Phase 0 was built on 19 Oct 2026 from a step-by-step setup guide (kept locally); this log was written up afterwards, on 30 Oct. Terms like "Volume" and "service account" are explained in the "Concepts" section at the bottom.

## How it all connects (read this first when you come back)

### The four platforms

```
Laptop (VS Code) ──git push──► GitHub ──deploy workflow──► Databricks: job + notebooks + src/
                                  │
                                  └──ingest workflow (nightly)──► reads Google Sheet ──► uploads JSON to the Volume
                                                                                    └──► starts the job ──► bronze
```

| Platform | Its job | How it's linked |
| --- | --- | --- |
| Google Sheets | Where I type expenses (the source of truth) | A service account has Viewer access; it can read, never edit |
| GitHub | Stores the code, runs the automation | Holds 4 secrets that let it log in to Google and Databricks |
| Databricks | Stores the data, runs the processing | GitHub logs in with a personal access token |
| Laptop | Where I write and test code | `git push` to GitHub; the Databricks CLI deploys directly when testing |

### One night, end to end

1. **02:00 SGT**, GitHub starts the `ingest` workflow (`.github/workflows/ingest.yml`).
2. It installs the project with uv and runs `uv run ingest-sheet` → `pull_sheet.py:main`.
3. `fetch_snapshot` logs in to Google as the service account and reads **every tab three ways**: `raw` (real numbers, dates as day counts), `formatted` (what you see on screen) and `formula` (the formulas themselves).
4. `upload_snapshot` saves all of it as one JSON file in the Volume: `/Volumes/workspace/bronze/raw/google_sheets/snapshot_<time>.json`.
5. The workflow runs `databricks bundle run`, which starts the job. Its bronze task (`notebooks/bronze/load_snapshots.py`) appends every snapshot that isn't in `bronze.sheet_rows` yet: one row per sheet row, cells kept exactly as fetched.

Why three ways? Each answers a different question later. `raw` gives unambiguous numbers and dates. `formatted` helps debug date mix-ups. `formula` shows hand-made rules like `=SUM(E2:E147)-E110`, which Phase 1 turns into flags.

## Step 0: plan

The project plan (`docs/analyst-agent-project-plan.md`) chose Databricks Free Edition, designed the push-from-outside ingest, and listed the sheet's 13 data-quality issues (section 3) that Phase 1 fixes.

## Step 1: install the tools

```powershell
winget install Git.Git
winget install Microsoft.VisualStudioCode
winget install Python.Python.3.12
winget install astral-sh.uv
winget install GitHub.cli
winget install Databricks.DatabricksCLI
```

Close and reopen PowerShell (and fully quit VS Code) afterwards, or new commands show "not recognized". Git identity uses the GitHub **noreply** address, so no real email appears in a public repo.

Check: `git --version`, `uv --version`, `gh --version`, `databricks --version` all print a version.

## Step 2: Databricks workspace

- **Catalog** in the left sidebar is called `workspace` (Free Edition's default).
- **SQL Editor:** ran `setup/01_create_schemas.sql`, which creates the `bronze`, `silver` and `gold` schemas and the `bronze.raw` Volume.
- **Avatar → Settings → Developer → Access tokens:** created a 90-day personal access token. It's shown once. Calendar reminder set for day 85.

Check (SQL Editor): `SHOW SCHEMAS IN workspace` lists `bronze`, `silver`, `gold`.

## Step 3: the repo and the Phase 0 files

- **`pyproject.toml` + `uv.lock`:** the project and its exact dependency versions. `uv sync` builds `.venv` from the lock file, so GitHub installs exactly what was tested.
- **`src/analyst_agent/ingest/pull_sheet.py`:** fetch and upload, explained above. Runs on GitHub, never on Databricks.
- **`notebooks/bronze/load_snapshots.py`:** snapshot JSON → `bronze.sheet_rows`. Skips snapshots already loaded, so re-running is safe.
- **`databricks.yml`:** the bundle: what to deploy. No workspace URL in it (public repo); the CLI reads it from `~/.databrickscfg` or the `DATABRICKS_HOST` secret.
- **`resources/*.job.yml`:** the job definition. No cluster is defined, so it runs on serverless compute (Free Edition's only option).
- **`.github/workflows/deploy.yml` and `ingest.yml`:** the automation.
- **`.gitignore`:** blocks `.env`, key files, `.xlsx` and `.csv`, so no data or secrets get committed.
- **`.env.example`:** a template for running the ingest locally.

## Step 4: Databricks CLI and first deploy

```powershell
databricks configure --host https://<workspace>.cloud.databricks.com --token
databricks current-user me          # prints your email: login works
databricks bundle validate          # "Validation OK!"
databricks bundle deploy            # creates the job "[dev <you>] ..."
```

`mode: development` in `databricks.yml` prefixes the job name with `[dev <you>]` and keeps everything in your user folder.

## Step 5: Google service account

1. The sheet is a native Google Sheet (no grey `.XLSX` badge).
2. Google Cloud Console → new project (no billing account) → enable the **Google Sheets API**.
3. IAM → Service Accounts → create `sheet-reader` → Keys → add a JSON key (a file downloads; treat it like a password).
4. In the sheet: Share → the service account's email → **Viewer** → untick Notify.

## Step 6: GitHub secrets and the first end-to-end run

```powershell
gh secret set DATABRICKS_HOST
gh secret set DATABRICKS_TOKEN
gh secret set SHEET_ID
$key = Get-Content "$HOME\Downloads\<key-file>.json" -Raw
$key | gh secret set GOOGLE_SA_JSON
Remove-Variable key
gh secret list                      # 4 names
gh workflow run deploy; gh run watch
gh workflow run ingest; gh run watch
```

Then the key file was deleted from Downloads: it now lives only in GitHub Secrets.

Check (SQL Editor):

```sql
SELECT tab, count(*) AS rows, count(DISTINCT snapshot_file) AS snapshots
FROM workspace.bronze.sheet_rows GROUP BY tab ORDER BY tab;
```

Expect every month tab plus the 2 templates. Run `ingest` again: each tab shows 2 snapshots and no duplicate rows within a snapshot. ✓ 19 Oct.

Commits: `383463a` "Phase 0: Databricks bundle, sheet ingest and deploy workflows", `e072fce` "README: full Phase 0 setup guide and architecture".

## Phase 0 summary

- **What you have:** a nightly copy of the whole sheet in `bronze.sheet_rows`, every night kept, and a deploy-on-push pipeline from GitHub to Databricks.
- **Safety:** read-only Google access; secrets only in GitHub Secrets and `~/.databrickscfg`; logs print counts only; no data in the repo.
- **Next:** Phase 1, cleaning bronze into `silver.transactions`.

## Open items

- **Token renewal:** the Databricks token expires every 90 days; update both `~/.databrickscfg` and the `DATABRICKS_TOKEN` secret.
- **Inactive Free Edition accounts may be deleted:** the nightly job keeps it active.

## Concepts

- **Databricks workspace:** your own Databricks environment, opened in a browser.
- **Unity Catalog → catalog → schema → table:** Databricks' three-level naming, e.g. `workspace.bronze.sheet_rows`. A schema is a folder of tables.
- **Volume:** a folder for *files* (not tables) inside a schema, e.g. `/Volumes/workspace/bronze/raw/`.
- **Delta table:** Databricks' table format. Supports overwrite, append, and history.
- **Serverless compute:** Databricks starts the machines for you when a job runs; no cluster to manage.
- **Personal access token (PAT):** a password-like key (`dapi...`) that lets the CLI and GitHub act as you.
- **Service account:** a robot Google account for programs. It has an email you can share a sheet with, and a JSON key instead of a password.
- **Scope:** what a Google login is allowed to do; `spreadsheets.readonly` can only read.
- **GitHub Actions / workflow:** a YAML file in `.github/workflows/` describing steps GitHub runs on its own computers.
- **Cron:** a timetable format. `0 18 * * *` = 18:00 UTC every day = 02:00 SGT.
- **GitHub Secret:** an encrypted value a workflow can use but nobody can read back.
- **Databricks Asset Bundle:** `databricks.yml` + `resources/*.yml`: a description of jobs and files that `databricks bundle deploy` creates or updates.
- **Medallion layers:** bronze (raw, as received), silver (cleaned, typed, tested), gold (shaped for use).
- **Snapshot:** a full copy of the sheet at one moment.
- **Idempotent:** running it twice gives the same result as once; the bronze loader skips snapshots already loaded.
- **uv / `uv.lock`:** the Python tool that installs packages; the lock file pins exact versions.
- **`.gitignore`:** files git must never track (data, keys, `.env`).
