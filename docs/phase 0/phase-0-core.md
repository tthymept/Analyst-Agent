# Phase 0: setup

Status (19 Oct 2026): all 6 steps done. **Phase 0 is complete.** Full details of every step: `phase-0-build-log.md` in this folder. Next: Phase 1 (pipeline).

## What Phase 0 builds

The plumbing, with no cleaning yet: four platforms connected so that my Google Sheet lands in Databricks every night, untouched.

- **Databricks Free Edition:** a workspace with three schemas (`bronze`, `silver`, `gold`) and a **Volume** (a folder for files) where raw snapshots land.
- **GitHub:** the repo and two workflows. `deploy` sends the code to Databricks on every push; `ingest` pulls the sheet every night.
- **Google Cloud:** a **service account** (a robot account) with Viewer access to the sheet, read-only.
- **My laptop:** VS Code, uv, the Databricks CLI and the GitHub CLI, to write code and deploy by hand when testing.

```
Google Sheet ──► GitHub Actions (ingest) ──► Volume: snapshot_<time>.json ──► bronze.sheet_rows
                  reads with the service account    one file per run            one row per sheet row
```

Done when: **all month tabs land in bronze**, and running ingest a second time adds one new snapshot without duplicating the first.

## Steps

0. Plan: platform choice (Databricks Free Edition), push-from-outside design, the 13 data-quality issues listed. **Done.**
1. Install the tools on the laptop: Git, VS Code, Python 3.12, uv, GitHub CLI, Databricks CLI. **Done.**
2. Databricks workspace: check the catalog name (`workspace`), run `setup/01_create_schemas.sql` (schemas + Volume), create a personal access token. **Done.**
3. Repo and Phase 0 files: `pyproject.toml`, `src/analyst_agent/ingest/pull_sheet.py`, `notebooks/bronze/load_snapshots.py`, `databricks.yml`, the job, both workflows, `.gitignore`, `.env.example`. **Done.**
4. Connect the Databricks CLI (`databricks configure`), `bundle validate`, `bundle deploy`, run the bronze job by hand. **Done.**
5. Google service account: Sheets API on, key created, sheet shared as Viewer. **Done.**
6. GitHub secrets (4) and the first end-to-end run: `deploy`, then `ingest` twice; check bronze has every tab and no duplicates. **Done.**

Commit status: all steps in `383463a` (code) and `e072fce` (README setup guide).

## Decisions

- **Databricks Free Edition** for everything (storage, jobs, SQL, dashboards, models): free with no time limit. Snowflake's trial lasts 30 days.
- **Push, don't pull:** Free Edition limits outbound internet, so GitHub fetches the sheet and uploads it; Databricks never calls Google.
- **Bronze keeps everything, unchanged:** every cell three ways (raw value, formatted text, formula), every night's copy. Cleaning happens later, and can always be re-run from bronze.
- **Full snapshot every night,** not just changes: the sheet is small (~1,700 rows) and I edit old rows.
- **Public repo, private data:** secrets only in GitHub Secrets and `~/.databrickscfg`; workflow logs print counts only.
- **Read-only Google access:** the service account is a Viewer and uses the `spreadsheets.readonly` scope.

## New ideas you'll meet

Explained in the build log:

- **Workspace, catalog, schema, table, Volume:** how Databricks organises data (Unity Catalog).
- **Service account:** a robot account for programs, with its own key.
- **Personal access token:** a password-like key for the Databricks CLI and GitHub.
- **GitHub Actions, workflow, cron, secret:** automation that runs on GitHub's computers.
- **Databricks Asset Bundle:** a YAML description of what to deploy (jobs, notebooks, code).
- **Medallion layers:** bronze (raw), silver (clean), gold (ready to use).
- **Idempotent:** running something twice gives the same result as once.
