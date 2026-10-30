# Phase 2 build log

What each step changed, how to test it, and its commit. Built on 30 Oct 2026. Unverified points are in "Open items"; terms like "fact table" are in "Concepts".

## How it all connects (read this first when you come back)

### The layers

```
silver.transactions
  │   notebooks/gold/build_gold.py: read → call → test → write
  ▼
gold.py
  fact_transaction(silver)        silver + month, is_living_spend, is_payback, living_amount
  dim_date(silver)                every day Jul 2025 – May 2029, term, is_tracked
  daily_living_spend(fact, dates) one row per tracked day (S$0 when nothing bought)
  monthly_summary(fact, dates)    one row per month with tracked days
  gold_tests(...)                 the tables agree; no spend on an untracked day
  │
  ▼
gold.* ──► dashboards/money.lvdash.json (SQL on gold) ──► AI/BI dashboard
       └─► the analyst agent (Phase 3)
```

### One question, end to end: "living spend per day this term"

1. Each silver expense row gets `living_amount = amount` if it's an everyday purchase; each friend's payback gets `living_amount = −amount`; everything else gets null (`fact_transaction`).
2. `dim_date` marks each day `is_tracked` if it lies between the first and last date of a month tab.
3. `monthly_summary` sums `living_amount` per month and counts tracked days.
4. Per day for the term = `SUM(living_spend) / SUM(tracked_days)` over the term's months. The dashboard's "by term" chart and the agent both compute exactly this, from the same table.

## Step 0: decisions

See `phase-2-core.md` → Decisions. The important one: living spend is defined once, in `gold.py`, and nothing downstream rebuilds the rule.

## Step 1: silver changes for money in

- **`rules.FUNDING_PATTERN`:** family money, allowance, tuition and hall money → `txn_kind = 'funding'`.
- **`rules.INCOME_PATTERN`:** interest, cashback, gifts, salary, wage → `income`. Any other money in (not funding, not a refund by name) → `refund`, i.e. a payback.
- **`rules.REFUND_PATTERN`:** `reimbur|refund` (catches a typo too).
- **`rules.ONE_OFF_ROWS`:** one tuition transfer whose name doesn't say so, listed by tab and row.

Order matters in the `txn_kind` rule: opening balance → adjustment → expense (money out) → refund by name → funding → income → otherwise refund.

Test: `uv run pytest` (updated tests: mid-tab family money is `funding`, interest and cashback are `income`).

## Step 2: `gold.py`

- **`fact_transaction`:** adds `month`, `is_living_spend` (expense, not one-off, not reimbursable), `is_payback` (refund, not reimbursable) and `living_amount`.
- **`dim_date`:** `sequence()` of every day from `rules.CALENDAR_START` to `CALENDAR_END`, `study_terms` reused from silver, `is_tracked` from each tab's first and last date.
- **`daily_living_spend`:** tracked days left-joined to spend, so a day with no purchases is S$0, and an untracked day has no row at all. A day can dip below 0 when a payback lands and nothing was bought.
- **`monthly_summary`:** living spend, purchases before paybacks, paybacks, one-offs, reimbursable paid and received, adjustments, funding, income, tracked days, and the daily average.
- **`gold_tests`:** total living spend must be the same in fact, daily and monthly; no transaction may fall on an untracked day. Errors stop the write.

## Step 3: notebook and job task

- `notebooks/gold/build_gold.py`: reads `silver.transactions`, builds the 4 tables, runs `gold_tests`, then overwrites `gold.*`.
- `resources/sheet_pipeline.job.yml`: new task `gold_tables`, depending on `silver_transactions`.

Test (after the push deploys):

```powershell
gh run watch                 # wait for "deploy" to finish first
gh workflow run ingest
```

Then **Catalog → workspace → gold** shows 4 tables. Starting `ingest` before the deploy finishes runs the old job (no gold task); that happened once, and a second run fixed it.

## Step 4: tests

`tests/transform/test_gold.py`, on made-up silver rows: an arrival-week purchase, a dinner and a friend's payback, a hall fee, work money paid and paid back, a correction, family money, interest, and a purchase in another term.

- `living_amount` only on the purchases (+) and the payback (−);
- tracked days only between each tab's first and last date; December untracked;
- a tracked day with no purchases is S$0; an untracked day has no row;
- the monthly numbers per column; the three tables agree.

```powershell
uv run pytest
```

Expect all tests to pass.

## Step 5: the gate

Gold's monthly living spend was compared with the sheet's own monthly totals (real data, read-only, 30 Oct). Results as counts:

- **6 of 13 months match exactly.**
- **3 differ only by `Correction` rows:** the sheet's total counts them, living spend doesn't.
- **The rest are explained row by row:** a `Total` formula subtracting ordinary meals (row numbers copied from another month), a reimbursable row the sheet kept, a shared meal the sheet left out with no payback recorded, and shared meals where gold counts my share (purchase minus payback) and the sheet leaves them out entirely.
- The first tab's sheet total is "August only" and leaves out the July arrival days; gold's calendar months match it.

✓ Gate met.

Commit: `c3455b7` "Gold layer, funding and paybacks".

## Step 6: the dashboard

A Databricks AI/BI dashboard is stored as one JSON file holding its **datasets** (SQL queries) and **widgets** (charts and their positions on a 6-column grid). This one was written as code, not by drag and drop:

1. **Datasets:** 6 SQL queries on gold (headline numbers, monthly, by term, purchases by category, daily, money map), each run read-only on real data first.
2. **`dashboards/money.lvdash.json`:** generated from those queries plus the widget specs: 3 counters, 4 bar charts, a Sankey, and a text header.
3. **`resources/money.dashboard.yml`:** deploys the file, with `dataset_catalog` and `dataset_schema: gold`, so the SQL uses bare table names.
4. **`databricks.yml`:** a `warehouse_id` variable that **looks up** the warehouse by name, so no ID goes into the public repo.

Check: `databricks bundle validate` → "Validation OK!"; after deploying, reading the dashboard back from the API showed all 6 datasets and 9 widgets stored, and the dashboard published.

To edit it later: change it in the Databricks UI, then pull the edits into the repo before the next push:

```powershell
databricks bundle generate dashboard --resource money --force
```

Commit: `f9e8c41` "AI/BI dashboard: Money in Singapore".

## Phase 2 summary

- **What you have:** 4 gold tables rebuilt nightly, one living-spend definition, and a dashboard on top.
- **Gate:** ✓ living spend reconciles with the sheet's monthly totals; every gap is explained.
- **Open:** the visual review of the dashboard.
- **Next:** Phase 3, the analyst agent.

## Open items

- **The money map (Sankey)** hasn't been seen rendered.
- **A payback lands on its own day,** not the day of the meal it pays for; daily numbers can dip below 0. Monthly and term totals are right.
- **New kinds of money in** (e.g. a salary named differently) become paybacks unless a word is added to `INCOME_PATTERN`.
- **Local Databricks CLI** is older than GitHub's; deploys print a warning. `winget upgrade Databricks.DatabricksCLI`.

## Concepts

- **Fact table:** one row per event (a transaction), with numbers you add up.
- **Dimension table:** one row per thing you describe or filter by (a day, with its term and weekday).
- **Dimensional modelling:** organising tables as facts and dimensions so questions become simple joins and sums.
- **Metric:** a named calculation with one definition (living spend), so every chart and answer agrees.
- **Grain:** what one row means ("one tracked day", "one month"). Mixing grains causes double counting.
- **Left join:** keep every row from the left table, fill blanks where the right has no match; how S$0 days appear.
- **Tracked vs untracked:** a day with no purchases (S$0) vs a day with no data. Averages divide by tracked days only.
- **AI/BI dashboard:** Databricks' built-in dashboard tool. **Dataset** = a SQL query; **widget** = a chart on a dataset.
- **Sankey diagram:** flows between stages drawn as bands, wider = more money.
- **Dashboards as code:** the dashboard's JSON lives in git and is deployed like the job.
- **Bundle variable lookup:** `lookup: warehouse: <name>` finds an ID at deploy time, so it's not written in the repo.
