# analyst-agent: project plan

A personal data platform that turns my money tracker (a Google Sheet) into clean, tested tables in Databricks, a dashboard, and an LLM analyst agent that answers questions about my spending in plain English. It's built to learn data engineering (pipelines, data quality, dimensional modelling) and AI engineering (tool calling, text-to-SQL, guardrails, evals) on real, messy data.

Status as of 30 Oct 2026: **Phase 0 (setup) done** (19 Oct): Databricks Free Edition, GitHub repo, Google service account, nightly ingest into bronze; details `docs/phase 0/`. **Phase 1 (pipeline) done** (30 Oct): `silver.transactions` cleaned in PySpark with data tests, and the balance check (`silver.balance_check`) shows every month's recomputed balance matches the sheet or differs only by a known sheet formula error; details `docs/phase 1/`. **Phase 2 (gold + dashboard) done** (30 Oct): 4 gold tables, monthly living spend reconciles with the sheet's own totals, AI/BI dashboard deployed by the bundle; the dashboard's visual review is still open; details `docs/phase 2/`. **Phase 3 (agent v1) started** (30 Oct): semantic layer, guarded `run_sql`, hand-written tool loop on a Databricks-hosted model, `uv run ask "..."`; next is the golden question set; details `docs/phase 3/`. A visitor's overview is in `README.md`.

"Verified" below means checked on my own data or workspace; "Not verified" means designed but not yet tested. Real amounts and names never appear in this repo (see section 8).

---

## 1. Goals and non-goals

**Goals**

- Ask questions about my money in plain English ("how much did I spend on food this term?") and get answers I can trust, with the SQL behind them.
- A pipeline that fixes the sheet's real data-quality problems in code, with tests, instead of by hand in formulas.
- One definition of "living spend", used everywhere: dashboard, agent, and any later dataset.
- An agent that is dataset-agnostic: the money tracker is the first dataset, not the only one.
- Everything free: Databricks Free Edition, GitHub Actions on a public repo, the Google Sheets API, Databricks-hosted models.
- Learn by building: each phase ends with something working and a write-up of what I learned.

**Non-goals**

- Replacing the sheet. The sheet stays my input app.
- Budgeting advice, investment advice, or anything that moves money.
- Publishing my real data. Anything public uses a synthetic dataset.
- Real-time updates. Once a day is enough for how often I log.

## 2. Architecture at a glance

The sheet stays the input. Once a day a GitHub Action pulls it and pushes a raw snapshot into Databricks. Everything after that runs inside Databricks, and the dashboard and the agent read only from gold.

```
Google Sheet ──► GitHub Actions ──push raw snapshot──► Databricks Free Edition
(I type here)    (nightly cron, Sheets API, read-only)
                                     ┌──────────────────────────────────────────────────┐
                                     │ Volume ──► Bronze ──► Silver ──► Gold             │
                                     │ (JSON)     (append)   (rebuild,   (rebuild,       │
                                     │                        tests)     tests)          │
                                     │                                    │      │       │
                                     │                      read-only SQL │   AI/BI      │
                                     │                                    ▼   dashboard  │
                                     │          LLM endpoint ◄──► Analyst agent ◄──► me  │
                                     └──────────────────────────────────────────────────┘
```

| Component | What it does | Where |
| --- | --- | --- |
| Google Sheet | My money tracker, one tab per month, updated every 1–5 days | Google Drive |
| Ingest | Reads every tab three ways (values, formatted, formulas) and uploads a JSON snapshot | `src/analyst_agent/ingest/`, GitHub Actions |
| Bronze | Every snapshot, unchanged, one row per sheet row | `workspace.bronze.sheet_rows` |
| Silver | One clean row per money movement, plus the balance check | `workspace.silver.*` |
| Gold | Facts, dates and metrics; the only layer anything downstream reads | `workspace.gold.*` |
| Dashboard | Databricks AI/BI dashboard on gold | `dashboards/`, deployed by the bundle |
| Analyst agent | Tool-calling loop: semantic layer → SQL → answer | `src/analyst_agent/agent/`, my laptop |
| LLM | A model hosted by Databricks, so data stays in Databricks | Databricks Foundation Model API |

**Push, don't pull:** Free Edition limits outbound internet, so a notebook calling the Google API may be blocked. The GitHub Action authenticates with a Google service account (Viewer on the sheet) and uploads through the Databricks Files API.

## 3. Upstream: the sheet

One tab per month (`8/25` … `10/26`), plus 2 template tabs. About 1,300 transactions so far. Coverage has gaps on purpose: I only log while I'm in Singapore, so there's no June or December tab, and July starts late.

Every problem below is a standard data-engineering problem, fixed in the pipeline rather than by hand.

| # | Issue | Example | Fix in the pipeline |
| --- | --- | --- | --- |
| 1 | One tab per month | `1026`, `926` … `825` | Read every month tab; derive the month from the tab name; skip templates |
| 2 | Schema drift | `Payment Method` disappears from Mar 2026; the first tab has no `Remaining` column and a different income header | Find columns by header name, not position; a missing column becomes null |
| 3 | Date only on the first row of each day | Several rows share one date | Fill each date down to the rows below it |
| 4 | Day/month swapped, or dates as text | 6 Jan read as 1 Jun; `13/1` stored as text | Try the date as stored and swapped; keep the one inside the tab's month |
| 5 | Summary rows mixed in with data | `Total`, `Avg`, `Normal` rows | Drop rows with no item and no category |
| 6 | Formulas as values | `=a+b` in an income cell | Read the computed value |
| 7 | Balance carried over typed as income | `Start`, `Monthly Start` at the top of a tab | `txn_kind = 'opening_balance'` for the tab's first money-in row |
| 8 | Hard-coded FX rates | The THB rate differs between months and columns | THB columns not read; a real rate table later |
| 9 | Wrong categories | A breakfast filed under Travel | Majority vote per item, plus explicit overrides |
| 10 | Inconsistent item names | `air con` / `Air-Con` / trailing spaces | `item_clean`: lower case, single spaces |
| 11 | Exclusions hidden in formulas | `=SUM(E2:E147)-E110-E74` | Explicit flags (`is_one_off`, `is_reimbursable`), not formulas |
| 12 | Big one-offs swamp the totals | Tuition and hall fees | Living spend leaves them out by definition |
| 13 | Derived columns stored as data | `Remaining`, `Daily Expense` | Not used as data; the balance check recomputes and compares |

## 4. Core component A: the pipeline

A Databricks job, `sheet_pipeline`, with three tasks in order. Started by the nightly ingest workflow.

### 4.1 Bronze: `bronze.sheet_rows` (append-only)

- One row per sheet row per snapshot, cells kept as JSON strings exactly as fetched (raw value, formatted text, formula).
- Snapshots already loaded are skipped, so re-running is safe.
- Bronze only grows: it keeps every night's copy, so I can see what the sheet looked like on any day.

### 4.2 Silver: `silver.transactions` and `silver.balance_check` (full rebuild)

- Rebuilt every night from the newest snapshot, in PySpark (`transform/silver_transactions.py`). The rules it uses live in `transform/rules.py`.
- One row per money movement. A sheet row with both money in and money out (a split bill) becomes two rows.
- **`txn_kind`:** `expense`, `opening_balance`, `funding` (family money: allowance, tuition and hall money), `income` (interest, cashback, small gifts), `refund` (paid back: by a friend, or for work), `adjustment` (`Correction` rows that fixed the sheet's balance).
- **Flags:** `is_one_off` (tuition, hall), `is_reimbursable` (work money paid and paid back), `excluded_in_sheet` (what the sheet's formulas subtracted, for comparison only), `study_year`, `semester`, `term`.
- **Data tests** run before every write. A missing or out-of-month date, an amount ≤ 0 or a duplicate ID fails the job and keeps the previous table.
- **Balance check** (the Phase 1 gate): each tab's closing balance is recomputed from the transactions and compared with the sheet's `Remaining`. Known gaps are sheet formula errors, each listed with its reason in `rules.KNOWN_BALANCE_DRIFT`; a new gap is a warning.

### 4.3 Gold (full rebuild)

| Table | One row per | Holds |
| --- | --- | --- |
| `fact_transaction` | money movement | Silver + `month`, `is_living_spend`, `is_payback`, `living_amount` |
| `dim_date` | day, 1 Jul 2025 – 31 May 2029 | study year, semester, term, weekday, `is_tracked` |
| `daily_living_spend` | tracked day | living spend, S$0 on days with no purchases |
| `monthly_summary` | month with tracked days | living spend and its daily average, one-offs, work money, funding, income |

**Living spend** (defined once, in `transform/gold.py`) = my own share of everyday spending: expenses that aren't one-offs or reimbursable, minus what friends paid back. Corrections are their own `txn_kind`, so they're already out. Cashback and small gifts are income and don't reduce it.

**`is_tracked`:** a day I was in Singapore and logging. It tells "spent S$0" apart from "no data".

## 5. Core component B: the dashboard

A Databricks **AI/BI dashboard**, "Money in Singapore", defined as code in `dashboards/money.lvdash.json` and deployed by the bundle on every push. It reads only from gold: headline numbers, monthly trend by term, purchases by category, per-day spend by term, daily spend, and a money map (Sankey: spend type → category). Edits made in the Databricks UI are pulled back into the repo with `databricks bundle generate dashboard`.

## 6. Core component C: the analyst agent

The agent writes SQL and lets the warehouse do the maths. The LLM's job is to understand the question, write and fix the query, and explain the result.

### 6.1 Tools

| Tool | Status | What it does |
| --- | --- | --- |
| `list_datasets` | Built | The catalogue, one line per dataset |
| `describe_dataset(name)` | Built | The semantic layer: tables, columns, rules, metrics, example SQL |
| `run_sql(sql)` | Built | One guarded `SELECT` on the dataset's tables; returns rows or an error to fix |
| `search_values(column, text)` | Phase 4 | Fuzzy or vector lookup of real values ("public transport" → bus and MRT items) |
| `make_chart(spec)` | Later | A chart spec the chat UI draws |

### 6.2 Semantic layer

`semantic/money.yml`, one file per dataset: every gold table and column in words, the rules ("spending" means living spend; no data is not S$0; per-day averages divide by tracked days), metric definitions in SQL, and example question → SQL pairs. Without it, an LLM happily adds up tuition and opening balances. When the agent gets a meaning wrong, this file is the first place to fix.

### 6.3 Guardrails

Checked in code (`agent/tools.py`) by parsing the SQL with `sqlglot` before it reaches Databricks:

- exactly one statement, `SELECT` only (no `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `DROP`, `SET`, even nested);
- only the dataset's own gold tables;
- `LIMIT 200` added when missing; 30 s timeout;
- a rejected query goes back to the model as an error, so it can fix it.

Not built yet: a separate read-only identity for the agent (see section 11).

### 6.4 Where RAG fits (and where it doesn't)

| Use | Worth it? | Why |
| --- | --- | --- |
| Embedding transaction rows to answer "how much did I spend" | No | Retrieval returns the top few rows, not all of them, so totals come out wrong. Aggregations belong in SQL |
| Fuzzy matching of item names | Yes | Hundreds of free-text item names with many spellings |
| Few-shot examples (the most similar verified question → SQL pairs) | Yes | The standard way to raise text-to-SQL accuracy |
| Routing across datasets | Once there are 3+ datasets | Keeps the prompt small |
| Free-text notes | Yes | Unstructured text is what RAG is for |

### 6.5 Evals

- A **golden question set**: 40–60 questions with answers computed by hand in SQL. Easy, multi-step, ambiguous and trick questions ("how much did I spend in June?" must answer "no data", not S$0).
- Measured: answer accuracy, tool calls, latency. Re-run after every prompt, model or semantic-layer change.
- **Baseline: Databricks Genie** on the same gold tables. The Phase 3 gate is beating it.

### 6.6 LLM

Behind one `chat` function, so swapping providers is one new function. Default: a model hosted by Databricks (Qwen 3.5 122B), so data stays in Databricks. A third-party model (Claude, Gemini) is a later option, with people's names replaced by tokens first. The loop is written by hand before reaching for a framework, to understand what frameworks do.

## 7. How everything connects

### 7.1 Repo layout

See `README.md` → Repo layout. Rule of thumb: **logic lives in `src/`, notebooks only read → call → write**, so the logic is tested on a laptop.

### 7.2 Wiring

- **Every push to `main`** → GitHub `deploy` workflow → `uv run pytest` → `databricks bundle deploy` (job, notebooks, `src/`, dashboard). A failing test deploys nothing.
- **Every night (02:00 SGT, often 1–4 h late)** → GitHub `ingest` workflow → pull the sheet → JSON snapshot in the Volume → start `sheet_pipeline`: bronze → silver → gold.
- **The agent** runs on my laptop with my Databricks CLI login: it reads the semantic layer from the repo, sends SQL to the Serverless Starter Warehouse, and calls the model endpoint.

### 7.3 Request flow: "how much did I spend in March?"

```
my question → loop.py → model: "call describe_dataset('money')"
→ semantic layer back to the model → model writes SQL on monthly_summary
→ tools.check_sql (one SELECT, gold only, LIMIT) → warehouse runs it → rows back to the model
→ model: the number, in S$, plus what it includes or leaves out
```

## 8. Security and privacy rules

- **The repo is public; the data is private.** No amounts, item names, people's names, sheet ID or workspace URL in code, tests, docs or commit messages. Sheet rows are referred to by tab and row number. Test fixtures are made up.
- **Secrets only in GitHub Secrets and `~/.databrickscfg`:** the Databricks token, the Google service-account key, the sheet ID, the workspace URL. Never printed: code on GitHub logs counts only, because workflow logs are public.
- **Read-only on the sheet:** the service account is a Viewer and uses the read-only Sheets scope.
- **The agent only reads:** one `SELECT` on gold, row limit, timeout.
- **Data stays in Databricks** by default (Databricks-hosted model). An outside LLM is my decision, with names replaced by tokens first.
- **Public demos use synthetic data** with the same schema and quirks.
- **Free tier, no billing:** the Google Cloud project has no billing account.

## 9. Roadmap

Each phase ends with something usable and a gate I can check.

**Phase 0: setup** (done 19 Oct 2026)
- Free Edition workspace, schemas and Volume; GitHub repo with deploy and ingest workflows; Google service account; nightly snapshots into `bronze.sheet_rows`.
- Done when: all month tabs land in bronze, and a second ingest adds one snapshot without duplicating. ✓ 19 Oct.

**Phase 1: pipeline** (done 30 Oct 2026)
- Silver cleaning for the 13 issues, data tests, nightly job; flags carried over from my old Power BI file (`is_reimbursable`, study year, semester); balance check.
- Done when: the recomputed balance matches the sheet. ✓ 30 Oct: every month with a `Remaining` column matches, or differs only by a sheet formula error that's been found and explained.
- Not done (optional): one long `Transactions` tab in the sheet.

**Phase 2: gold and dashboard** (done 30 Oct 2026)
- Gold fact and dimension tables, living-spend definition, AI/BI dashboard with the money map.
- Done when: living spend matches the sheet's own monthly totals. ✓ 30 Oct: 6 of 13 months match exactly, 3 differ only by `Correction` rows, and every other gap is explained row by row.
- Still open: my visual review of the dashboard.

**Phase 3: agent v1** (started 30 Oct 2026; steps in `docs/phase 3/phase-3-core.md`)
- Semantic layer, guarded tools, hand-written loop, golden question set, simple chat UI, synthetic dataset for public demos.
- Done when: the agent beats Genie on the golden question set.

**Phase 4: RAG and evals**
- `search_values` (vector search on item names), few-shot SQL examples, MLflow tracing, a second dataset (workouts), CSV upload.
- Done when: the agent answers a question across money and workouts.

**Phase 5: stretch** (optional)
- Port the transformations to dbt; run gold on a 30-day Snowflake trial.
- Done when: the same models run on two warehouses.

## 10. Testing

- `uv run pytest`: transform tests on a small fake workbook with the same quirks as the real sheet (local Spark), gold tests, and agent tests with a scripted fake model (no network).
- Runs on my laptop and again on GitHub before every deploy.
- On Databricks: silver data tests and gold consistency tests run inside the job, before any table is overwritten.
- Checks against real data are run by hand (read-only queries) and recorded in the build logs as counts and pass/fail, never amounts.

## 11. Open questions

- **Read-only identity for the agent:** today `run_sql` runs with my own login, guarded by the SQL parser. A separate identity with SELECT on gold only would be stronger. Not verified whether Free Edition supports it.
- **Dashboard rendering:** every widget was accepted by Databricks, but nobody has looked at the money map (Sankey) rendered yet.
- **Sharing on Free Edition:** whether I can add other users to the workspace. Not verified.
- **Two money-in rows** whose meaning I haven't confirmed (one in `3/26`, one in `10/25`): are they paybacks or work money? They're treated as paybacks for now.
- **One long `Transactions` tab** in the sheet from now on: optional, my call.
- **Base currency and categories:** SGD with THB as a display conversion? Keep the 8 categories, or split Food?
- **dbt or PySpark:** built in PySpark for now; dbt is a Phase 5 option.
- **Local Databricks CLI** is older than the one GitHub uses; deploys work but print a warning.

## Appendix A: Verified facts (my workspace and data, 19–30 Oct 2026)

- **Sheet:** 13 month tabs and 2 template tabs; column layout changes three times; the first tab has no `Remaining` column.
- **Ingest:** reads every tab in ~1 minute; a nightly run uses about 0.5 DBU, far inside the free limit.
- **GitHub schedule:** the 02:00 SGT cron actually started between about 04:20 and 06:30 SGT.
- **Databricks block (27–29 Oct):** new job runs were refused account-wide ("Triggering new runs … disabled temporarily"). Not caused by usage (steady ~0.5 DBU/day). Snapshots still landed; one manual run on 30 Oct caught up.
- **Race between workflows:** an ingest started right after a push can run the job before the deploy finishes, using the old job definition.
- **Balance check:** every month with a `Remaining` column either matches or differs only by one of 6 sheet formula errors (a row that never subtracts its expense, or skips the row above). Some `Correction` rows only patched these errors.
- **Gold vs sheet totals:** 6 of 13 months match exactly; 3 differ only by `Correction` rows; the rest are explained row by row (a stale formula, a reimbursable row, shared meals).
- **Hosted models (30 Oct):** Qwen 3.5 122B, GPT-OSS 120B and Llama 4 Maverick answered 4 test questions with correct numbers; Llama 4 once said "S$0" for a month with no data; Llama 3.3 70B got the first question wrong. One listed endpoint was retired.
- **Dashboards as code:** the CLI deploys a `.lvdash.json` dashboard from the bundle; the warehouse is looked up by name.

## Appendix B: Tech stack

| Tool | What for |
| --- | --- |
| Google Sheets API | Reading the sheet (read-only scope) |
| GitHub Actions | Nightly ingest; test-then-deploy on every push |
| Databricks Free Edition | Unity Catalog, Volumes, Delta tables, serverless jobs, SQL warehouse, AI/BI dashboards, model serving |
| Databricks Asset Bundles + CLI | Deploying the job and the dashboard from the repo |
| Python 3.12 + uv | Code and pinned dependencies (`uv.lock`) |
| PySpark | Silver and gold transformations; local Spark for tests |
| pytest + JDK 21 | Tests, locally and on GitHub |
| sqlglot | Parsing the agent's SQL for the guardrails |
| PyYAML | Reading the semantic layer |
| databricks-sdk | The agent's calls to the warehouse and the model |

## Appendix C: Feature backlog

| Feature | What it does | What I learn |
| --- | --- | --- |
| Runway | "At this month's rate, my balance lasts N days" | Window functions, metric definitions |
| Personal price index | How the price of repeat items changes over time | Slowly changing data |
| Weekly digest | An LLM-written summary every Sunday | Scheduled jobs, grounding text in SQL results |
| Anomaly alerts | Days far above my rolling average | Statistics in SQL, alerting |
| Month-end forecast | Projected spend by month end | Simple time-series modelling, MLflow |
| Chat-to-log | Send "chicken rice 5.5" to a bot and it becomes a sheet row | Structured output and validation |
| Receipt photo → row | A vision model extracts item, amount and date | Multimodal extraction |
| Auto-categoriser | Suggests a category for new items | Embeddings, classification |
| Real FX layer | Daily SGD → THB rates | External reference data |
| CSV upload | Any CSV becomes a dataset the agent can query | Schema inference, metadata catalogues |
| Cross-dataset questions | "Do I spend more on food on gym days?" | Joining domains on a shared date dimension |

## Sources

- [Databricks Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations): quotas, outbound internet, Apps, vector search
- [Databricks Free Edition overview](https://docs.databricks.com/aws/en/getting-started/free-edition): included features
- [Databricks Community: Free Edition outbound internet blocked](https://community.databricks.com/t5/data-engineering/free-edition-outbound-internet-suddenly-blocked-error/m-p/132638): why data is pushed in from outside
