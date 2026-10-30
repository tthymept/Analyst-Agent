# Phase 1 build log

What each step changed, how to test it, and its commit. Steps 0–4 were built on 19 Oct 2026, step 5 on 20 Oct, steps 6–7 on 30 Oct; this log was written up on 30 Oct. Unverified points are in "Open items" at the bottom; terms like "window function" are in "Concepts".

## How it all connects (read this first when you come back)

### The layers

```
bronze.sheet_rows (one row per sheet row, every snapshot, cells as JSON strings)
  │   notebooks/silver/build_transactions.py: read → call → write
  ▼
build_silver(bronze)              src/analyst_agent/transform/silver_transactions.py
  │   uses the rules in rules.py
  ▼
data_tests(silver)  ── an error? stop, keep yesterday's table
  │
  ▼
silver.transactions ──► balance_check(silver) ──► silver.balance_check
```

The notebook knows nothing about cleaning. `silver_transactions.py` knows nothing about where its DataFrame came from. `rules.py` knows nothing about Spark. That's why the whole thing can be tested on a laptop with made-up rows.

### One sheet row, end to end

Take a row in tab `1/26` with a blank date, an item, category `Food`, an income cell and an expense cell (a shared meal where someone paid me back part).

1. `latest_snapshot` keeps only the newest snapshot.
2. `month_tabs` keeps tabs named like `1/26` and reads the month (1) and year (2026) from the name.
3. `named_columns` reads row 1 of the tab, finds "Expense(SGD)" etc. by **header name**, and pulls each cell out of the row by that position.
4. `resolve_dates`: the date cell is blank, so the date is **filled down** from the last row above that had one.
5. `clean_items`: `item_clean` = lower case, single spaces.
6. `to_transactions`: the row has money in *and* money out, so it becomes **two transactions** ("legs"): `out` (`expense`) and `in` (`refund`).
7. `fix_categories`: if the item appears 3+ times and most often as `Food`, it gets `Food` even if I typed something else this once.
8. `flag_one_offs`, `flag_reimbursable`, `study_terms`: the flags and the term (`Y1S2`).
9. `txn_id` = a hash of tab, row, direction, item and amount: the same row always gets the same ID.

## Step 0: design

- One function per cleaning step, each taking and returning a DataFrame, read top to bottom in `build_silver`.
- Logic in `src/`, notebook only reads → calls → writes.
- Silver is a full rebuild from the newest snapshot (see `phase-1-core.md`).

## Step 1: `rules.py`

The business rules, kept apart from the Spark code so they're easy to find and change:

- `MONTH_TAB_PATTERN`: tabs like `8/25`; anything else (`EMPTY`) is skipped.
- `HEADER_ALIASES`: header text → column name, compared after lower-casing and dropping non-letters, so "Remaining (SGD)" and "remaining(sgd)" match.
- `OPENING_BALANCE_PATTERN`, `REFUND_PATTERN`, `ADJUSTMENT_PATTERN`: regular expressions on `item_clean`.
- `ONE_OFF_PATTERNS`, `EXPENSE_CATEGORIES`, `CATEGORY_OVERRIDES`, `CATEGORY_VOTE_MIN_ROWS`.

THB, `Remaining` and `Daily Expense` are derived in the sheet, so they're deliberately not read as data.

## Step 2: `silver_transactions.py`

The steps listed above, plus:

- **`decode`:** bronze cells are JSON strings (`'"Food"'`, `'2.5'`, `'""'`); wrapping in `{"v": ...}` lets one JSON path read all of them.
- **`try_cast`, not `cast`:** serverless runs in ANSI mode, where a bad cast fails the job instead of returning null.
- **`resolve_dates`:** a date is either a serial number (days since 1899-12-30) or text like `13/1`. Two readings are tried (as stored, and day/month swapped); the first inside the tab's month wins, else the previous month. `date_fix` records which (`serial:swapped`, `text:as_is`, ...).
- **`sheet_exclusions`:** reads the `Total` formulas (`=SUM(E2:E147)-E110-E74`) and flags the subtracted rows as `excluded_in_sheet`, for comparison only.
- **`data_tests`:** errors (missing date, date outside its tab's month, amount ≤ 0, duplicate `txn_id`) and warnings (unknown category, category changed by cleaning).

## Step 3: tests

- `tests/conftest.py` starts a local Spark (needs JDK 17 or 21) in ANSI mode and UTC, like Databricks serverless.
- `tests/transform/test_silver_transactions.py` builds a fake workbook (made-up rows) with the same quirks: swapped dates, a text date, a wrong category, a split bill, a correction, a `Total` row, three column layouts, an older snapshot that must be ignored.

Test (PowerShell, in the project folder):

```powershell
uv run pytest
```

Expect all tests to pass in about a minute.

## Step 4: notebook, job and deploy

- `notebooks/silver/build_transactions.py`: reads `bronze.sheet_rows`, calls `build_silver`, runs `data_tests`, prints them, raises if any error-level test failed, then overwrites `silver.transactions`.
- `resources/sheet_pipeline.job.yml`: task `bronze_load`, then `silver_transactions` (which depends on it). The notebook gets `src_path` so it can import the repo's `src/`.
- `deploy.yml`: `uv run pytest` first (with Java 21), then `databricks bundle deploy`. A failing test deploys nothing.

Test:

```powershell
databricks bundle deploy
databricks bundle run sheet_pipeline
```

Then in the SQL Editor:

```sql
SELECT source_tab, txn_kind, count(*) FROM workspace.silver.transactions GROUP BY ALL ORDER BY 1, 2;
```

Expect every month tab with `expense` rows, and `opening_balance` rows on tabs that start with one.

Commit: `0f127ac` "Phase 1: silver.transactions in PySpark with data tests".

## Step 5: flags from the old Power BI file

Before this project I cleaned the sheet by hand in an Excel file linked to Power BI. Its columns map to silver like this:

| Power BI column | Silver |
| --- | --- |
| `Is_Reimbursable` | `is_reimbursable` |
| `Is_Adjustment` | `txn_kind = 'adjustment'` (already handled by the `Correction` pattern) |
| `Year`, `Semester` | `study_year`, `semester`, `term` |

- **`flag_reimbursable`:** true if the row is listed in `rules.REIMBURSABLE_ROWS` (by tab and sheet row number, matched once against the old file) or its Note cell contains `reimb`. Applies to both legs: the money I paid out and the money paid back.
- **`study_terms`:** semester from the month (Jul–Dec = 1, Jan–May = 2), study year from the academic year starting in July, `term` like `Y1S2`. June gets null, and a warning test counts such rows.

Test: `uv run pytest` (new tests: reimbursable from the row list and from the Note cell; terms for a July, a January and a September row).

Commits: `d036b85` "Add is_reimbursable, study_year, semester and term to silver.transactions", `1f80cfa` "README: reimbursable, study terms, deploy notes".

## Incident: Databricks refused new job runs (27–29 Oct)

Three nightly runs failed at "start the job" with *"Triggering new runs for organization … is currently disabled temporarily."* The pull from Google still worked, so the snapshots landed in the Volume.

- **Not our code:** the job never started.
- **Not usage:** the billing table showed a steady ~0.5 DBU per night.
- **Recovery:** one manual run on 30 Oct loaded all three waiting snapshots, because the bronze loader appends every snapshot not loaded yet.
- **Lesson:** bronze-from-files makes a missed night harmless. Check failures with `gh run list --workflow ingest`.

## Step 6: balance check (the Phase 1 gate)

- **`sheet_balance`:** silver now keeps the sheet's own `Remaining` value on each row.
- **`balance_check(silver)`:** per tab: opening balance (the first row's sheet balance minus that row's own money), the recomputed closing balance (opening + running net), the sheet's closing balance, and the `drift` between them.
- **`rules.KNOWN_BALANCE_DRIFT`:** each tab's known gap, with the sheet formula error that causes it (e.g. "rows 60–61 never subtract the expense (=H59+E60)"). A gap that isn't listed, or doesn't match, makes `explained = false` and a `balance_unexplained` warning.
- The notebook writes `silver.balance_check` after silver.

What it found (real data, 30 Oct): every month with a `Remaining` column either matches exactly or differs by one of 6 sheet formula errors: a row that never subtracts its expense, a row that skips the row above, a row pointing at the wrong row. Some `Correction` rows in the sheet existed only to patch these.

Test:

```sql
SELECT source_tab, drift, known_drift, explained FROM workspace.silver.balance_check ORDER BY source_tab;
```

Expect `explained = true` on every row with a drift (the first tab has no `Remaining` column, so its drift is null). ✓ 30 Oct.

Commit: `2c599f1` "Balance check: silver.balance_check, the phase 1 gate".

## Step 7: opening balances from the first row

Two tabs start with a row named like income rather than "Start", so they were counted as income. Rule changed: **a tab's first money-in row is starting money, whatever it's called**; the name pattern stays as a backup (e.g. cash on hand on the second row).

- The first row per tab is found with a **window function** (`min(row_num)` over each tab), computed as its own column first, because Spark doesn't allow a window function inside `explode`.

Test: `uv run pytest` (new test: a first-row "Monthly Income" is `opening_balance`; the same name further down is not; a first row that's an expense is still an expense).

Commit: `649d4b3` "Balance check and first-row opening balances".

## Phase 1 summary

- **What you have:** `silver.transactions` (one row per money movement, typed, flagged, tested) and `silver.balance_check` (the gate), rebuilt nightly.
- **How it's built:** `rules.py` (what) → `silver_transactions.py` (how, PySpark) → notebook (read, test, write).
- **Gate:** ✓ the recomputed balance matches the sheet, apart from 6 explained sheet formula errors.
- **Next:** Phase 2, gold tables and the dashboard.

## Open items

- **New sheet formula errors** show up as `balance_unexplained` warnings in the job output; nobody is alerted. Check the job output now and then, or add an alert later.
- **Fixing the formulas in the sheet** would make those months "unexplained" (the gap no longer matches); then remove them from `KNOWN_BALANCE_DRIFT`.
- **Row-number lists** (`REIMBURSABLE_ROWS`, `ONE_OFF_ROWS`) break if rows are inserted in an old tab. Closed months don't change, so it's safe for now.
- **Two categories changed by cleaning** stay as warnings; worth a look once.

## Concepts

- **PySpark DataFrame:** a table you transform with code; nothing runs until you write or collect it (lazy).
- **Column expression:** `F.col("amount") > 0`; a formula applied to every row.
- **`withColumn` / `select` / `filter` / `groupBy`:** add a column / pick columns / keep rows / aggregate.
- **Window function:** a calculation over a group of rows without collapsing them, e.g. "the last date above this row in the same tab" (fill down) or "the smallest row number in this tab".
- **`explode`:** turns one row with a list into several rows: how one sheet row becomes two legs.
- **ANSI mode:** strict SQL rules; a bad cast is an error, so `try_cast` is used.
- **Serial date:** a date stored as days since 1899-12-30 (Google Sheets' format).
- **Regular expression (regex):** a text pattern, e.g. `^correction`.
- **Majority vote:** the most common label for an item wins over one-off typos.
- **Hash (`sha2`):** a fingerprint of some text; same text → same hash. Used for a stable `txn_id`.
- **Data test:** a check before writing; errors stop the write, warnings are printed.
- **Reconciliation:** comparing your result with an independent number (the sheet's balance) to prove nothing was lost or double counted.
- **Drift:** the gap between two numbers that should agree.
- **Full rebuild vs incremental:** recompute everything each time vs only apply changes.
- **pytest fixture:** a helper a test receives by naming it (here, the local Spark session and the fake silver table).
