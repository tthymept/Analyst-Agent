# analyst-agent

A personal data platform: my expense tracker (Google Sheet) → Databricks (bronze / silver / gold) → AI/BI dashboard → an LLM analyst agent that answers questions in plain English. I'm learning data engineering and AI engineering through it, so explaining matters as much as building.

Docs: `docs/analyst-agent-project-plan.md` (the whole plan), `docs/phase N/phase-N-core.md` (one phase, short), `docs/phase N/phase-N-build-log.md` (every step in detail). `README.md` is the visitor's overview.

## Environment

- Windows 11, PowerShell. Python 3.12+ with **uv** (`uv sync`, `uv run ...`).
- Local tests run PySpark, which needs a **JDK 17 or 21** (see `tests/conftest.py`).
- Databricks Free Edition, catalog `workspace`, schemas `bronze` / `silver` / `gold`. The Databricks CLI is logged in through `~/.databrickscfg`.
- GitHub repo `tthymept/Analyst-Agent` is **public**.

## Commands

```powershell
uv run pytest                                  # all tests (~1 min, local Spark + agent tests)
uv run ask "How much did I spend in March 2026?"   # the analyst agent (add --sql to see its queries)
databricks bundle validate                     # check the bundle (job + dashboard)
gh workflow run ingest                         # pull the sheet now and rebuild bronze → silver → gold
gh run watch                                   # follow a GitHub Actions run
```

A `git push` to `main` runs the tests, then `databricks bundle deploy`. The nightly `ingest` workflow (02:00 SGT, often 1–4 h late) pulls the sheet and runs the job.

## Architecture rules

- **Logic lives in `src/`, notebooks only read → call → write.** The logic is tested on a laptop; notebooks stay short.
- Layers: Google Sheet → `ingest/pull_sheet.py` (GitHub Actions) → `bronze.sheet_rows` (append-only snapshots) → `silver.transactions` (full rebuild, data tests) → `gold.*` (full rebuild, consistency tests) → dashboard and agent.
- **Business rules live in `src/analyst_agent/transform/rules.py`.** Change a rule there first, not inside the transform code.
- **Living spend is defined once**, in `gold.py` (`is_living_spend`, `is_payback`, `living_amount`). Everything downstream reads it from gold; nothing rebuilds the rule.
- The dashboard and the agent read **only from `gold`**.
- The agent: `semantic/money.yml` (meaning) → `agent/tools.py` (tools, SQL guardrails) → `agent/loop.py` (tool-calling loop, model behind one `chat` function).

## Non-negotiable rules

- **No real data in the repo.** No amounts, item names, people's names, sheet IDs or workspace URLs in code, tests, docs or commit messages. Refer to sheet rows by tab and row number (`10/25` row 7), never by name. Test fixtures are made up.
- **Docs are public too:** describe what was checked ("6 of 13 months match exactly"), not the real numbers.
- **Never print or log secrets or amounts** in code that runs on GitHub (workflow logs are public): counts and statuses only.
- **The agent only reads:** `run_sql` allows one `SELECT` on the gold tables, with a row limit and a timeout. Never widen this without asking.
- **Data stays in Databricks:** the agent uses a Databricks-hosted model by default. Sending data to an outside LLM is the user's decision, and people's names should be replaced with tokens first.
- **Ask before anything that changes Databricks or GitHub:** running the job, deploying, pushing, deleting. Read-only queries are fine.
- **If something isn't verified, say so.** Mark a finding as verified only after checking it.

## How to answer the user

- **Pace: one step at a time when building** (code, pipelines, anything the user runs or tests). Build one step, explain it, give the test, then stop and wait for "next". Don't run ahead into the next step. Documentation-only work can be done in one go.
- Start with 2–5 lines on what the step builds and why. If there's a better approach, say so first.
- One file per step (several edits to one file = 1a, 1b). Say where each edit goes.
- The user is learning PySpark, Databricks, SQL and LLM agents: **explain each new concept the first time it appears, tied to the actual code**, and add it to the build log's Concepts list.
- End each step with how to test it (what to run, what to expect) and honest caveats.
- After each step, update that phase's build log (what changed, test, commit) and the step's line in the phase core file.
- Debugging: ask for the exact error or output first.
- Warm, direct, plain words.

## Commits

Conventional Commits: `type(scope): summary` (imperative, lowercase, no period, ~72 chars), e.g. `feat(agent): add golden question set`. When proposing a commit, give a short version and a version with a bullet body. The user pushes.
