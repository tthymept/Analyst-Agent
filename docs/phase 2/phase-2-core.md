# Phase 2: gold and dashboard

Status (30 Oct 2026): steps 0–6 done. **Phase 2's gate is met:** monthly living spend reconciles with the sheet's own totals. Still open: my visual review of the dashboard (step 7). Full details of every step: `phase-2-build-log.md` in this folder. Next: Phase 3 (agent v1).

## What Phase 2 builds

The layer everything downstream reads, and the first thing built on it:

- **Gold tables** (`src/analyst_agent/transform/gold.py`): `fact_transaction`, `dim_date`, `daily_living_spend`, `monthly_summary`. Rebuilt nightly as the job's third task, after silver.
- **One definition of living spend**, in code, used by everything.
- **An AI/BI dashboard**, "Money in Singapore", defined as a file in the repo and deployed by the bundle.

```
silver.transactions ──► gold.py ──► gold tests ──► gold.fact_transaction ─┬─► dashboard
                                                   gold.dim_date          ├─► analyst agent (Phase 3)
                                                   gold.daily_living_spend│
                                                   gold.monthly_summary ──┘
```

Done when (from the plan): **living spend matches my `Normal` totals.** The sheet has no consistent `Normal` row; each tab's `Total` formula subtracts rows by hand. So the check became: gold's monthly living spend vs the sheet's monthly totals, with every gap explained.

## What "living spend" means

My own share of everyday spending:

- **in:** expenses (`txn_kind = 'expense'`);
- **out:** one-offs (tuition, hall fees), work money paid and paid back (`is_reimbursable`), `Correction` rows (their own `txn_kind`);
- **minus:** what friends paid back for shared spending (`is_payback`).

Cashback and small gifts are income: they don't reduce it. In gold, each row carries `living_amount` (+ purchase, − payback, null otherwise), so `SUM(living_amount)` is living spend everywhere.

## Steps

0. Decisions: funding vs income, one-offs, living-spend definition, paybacks (my answers on 30 Oct). **Done.**
1. Silver changes: `txn_kind = 'funding'` (family money), income = interest/cashback/gifts only, other money in = `refund`; a tuition transfer listed as a one-off by row. **Done.**
2. `src/analyst_agent/transform/gold.py`: the 4 tables, `is_living_spend`, `is_payback`, `living_amount`, and `gold_tests` (the 3 tables agree on total living spend; no transaction on an untracked day). **Done.**
3. `notebooks/gold/build_gold.py` + job task `gold_tables` (after silver). **Done.**
4. `tests/transform/test_gold.py`: made-up silver rows covering every kind of money. **Done.**
5. The gate: monthly living spend vs the sheet's totals, every gap explained. **Done.**
6. Dashboard: `dashboards/money.lvdash.json` (6 SQL datasets, 9 widgets), `resources/money.dashboard.yml`, warehouse looked up by name in `databricks.yml`. **Done.**
7. Review the dashboard in Databricks (especially the money map); polish in the UI; pull edits back with `databricks bundle generate dashboard`. **Open.**

Commit status: `c3455b7` (steps 0–5), `f9e8c41` (step 6).

## Decisions

Made 30 Oct 2026.

- **Funding is separate from income:** family money, allowance, tuition and hall money are `funding`. They're not my personal spending money when they pay tuition.
- **One-offs = tuition and hall fees only** (plus one tuition transfer listed by row).
- **Living spend = my own share:** purchases minus friends' paybacks. Chosen over "leave shared meals out entirely", so future split bills are handled automatically.
- **Cashback and small gifts count as income,** so they don't reduce living spend.
- **Months are calendar months** of the transaction date, not sheet tabs. The first tab starts in late July, and the sheet's own August total leaves those days out too.
- **`is_tracked` days:** between the first and last date of each month tab. That's how "spent S$0" is told apart from "no data" (in Thailand).
- **Dashboard as code:** the `.lvdash.json` file is the source; the bundle deploys it on every push.

## Changes from the original plan

- **No `dim_item` or `dim_fx_rate` yet:** item aliases wait for Phase 4 (`search_values`); FX rates are a backlog item.
- **No metric views yet:** the living-spend definition lives in `gold.py` and in the semantic layer; Databricks metric views are an option later.
- **Money map is spend type → category,** not funding → category → item: funding doesn't map to specific purchases.
- **The gate was redefined** (see above) because the sheet has no consistent `Normal` row.

## Things to check

- **The money map (Sankey) renders:** Databricks accepted every widget, but nobody has seen it drawn yet. Not verified.
- **Pull-before-push:** a `git push` overwrites edits made in the dashboard UI unless they were pulled into the repo first.
- **The workflow race:** start `ingest` only after the `deploy` run finishes, or the job runs the old definition.

## New ideas you'll meet

- **Fact and dimension tables** (dimensional modelling).
- **Metric definition in one place.**
- **Tracked vs untracked days** (missing data isn't zero).
- **AI/BI dashboard, dataset, widget,** and dashboards as code.
