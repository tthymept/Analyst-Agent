# Phase 1: pipeline (silver)

Status (30 Oct 2026): all 8 steps done. **Phase 1 is complete.** The balance check shows every month's recomputed balance matches the sheet, or differs only by a sheet formula error that's been found and explained. Full details of every step: `phase-1-build-log.md` in this folder. Next: Phase 2 (gold and dashboard).

## What Phase 1 builds

`silver.transactions`: one clean row per money movement, rebuilt every night from the newest bronze snapshot, in PySpark. It fixes the sheet's data-quality problems in code (plan section 3) and refuses to overwrite the table if its data tests fail.

```
bronze.sheet_rows ──► build_silver() ──► data tests ──► silver.transactions ──► balance_check() ──► silver.balance_check
(raw cells)            (src/, PySpark)    (pass → write)  (1 row per money move)   (phase 1 gate)
```

Done when (from the plan): **the recomputed balance matches the sheet**: each month's closing balance, rebuilt from the transactions, equals the sheet's own `Remaining` column.

## Why silver is a full rebuild

Silver is thrown away and rebuilt every night from the newest snapshot. The sheet is small (~1,700 rows), so a rebuild takes seconds, and it can never drift out of sync with the sheet the way an incremental update could (I edit old rows). Bronze keeps the history; silver is always "the sheet as it is now, cleaned".

## Steps

0. Design: one function per cleaning step, logic in `src/` so it's tested on a laptop, notebooks only read → call → write. **Done.**
1. `src/analyst_agent/transform/rules.py`: the business rules: tab-name pattern, header aliases, opening-balance / refund / adjustment patterns, one-off patterns, categories, category overrides and the majority-vote threshold. **Done.**
2. `src/analyst_agent/transform/silver_transactions.py`: the steps: latest snapshot → month tabs → columns by header name → dates fixed and filled down → summary rows dropped → money in / money out legs and `txn_kind` → categories by majority vote → one-off flags → `txn_id`. Plus `data_tests`. **Done.**
3. `tests/`: local Spark (`conftest.py`) and a small made-up workbook with the same quirks as the real one. **Done.**
4. `notebooks/silver/build_transactions.py` + the job: bronze task then silver task; error-level test failures stop the write. Deploy workflow runs pytest first. **Done.**
5. Flags carried over from my old Power BI file: `is_reimbursable` (rows listed by tab and row number, or `reimb` in the Note cell), `study_year`, `semester`, `term`. **Done.**
6. Balance check: `sheet_balance` column, `balance_check()`, `silver.balance_check` table, known sheet formula errors in `rules.KNOWN_BALANCE_DRIFT`. **Done.**
7. Opening balances: a tab's first money-in row is starting money whatever it's called. **Done.**

Commit status: `0f127ac` (steps 0–4), `d036b85` + `1f80cfa` (step 5), `2c599f1` (step 6), `649d4b3` (step 7).

## Decisions

- **PySpark in `src/`**, not SQL notebooks or Lakeflow: testable on a laptop with pytest, and the same code runs on Databricks. (The plan's Lakeflow / dbt question stays open for Phase 5.)
- **Columns found by header name,** not position, because the layout changes three times.
- **Dates:** try the cell as stored and with day and month swapped; keep whichever falls in the tab's month (the previous month is allowed, since a tab can start a few days early).
- **Categories:** an item seen 3+ times takes its majority category (if more than half agree), then explicit overrides. `category_raw` keeps what I typed.
- **One-offs = tuition and hall fees only** (decided 30 Oct).
- **Reimbursable rows** are listed by tab and row number in `rules.py`, never by item name, because item names contain friends' names and the repo is public. New ones: `reimb` in the Note cell.
- **Semesters:** Aug–Dec = 1, Jan–May = 2; late-July arrival days count as semester 1; June has no semester. Year 1 starts Aug 2025.
- **Adjustments:** `Correction` rows become `txn_kind = 'adjustment'`, not spending.
- **The balance check is a warning, not an error:** a typo in the sheet shouldn't stop the nightly update.

## Changes from the original plan

- **No new `Transactions` tab in the sheet** (yet): silver copes with the monthly tabs, so it's optional.
- **`excluded_in_sheet` is kept for comparison only,** not trusted as the one-off rule: the sheet's formulas get copy-pasted between months with old row numbers in them.
- **The gate became "matches, or differs only by a known sheet error"**: the sheet's own `Remaining` formulas have mistakes, so an exact match everywhere would mean the pipeline copied them.
- **More `txn_kind` values** than planned (`funding` came in Phase 2).

## New ideas you'll meet

Explained in the build log:

- **PySpark DataFrame, column expression, window function.**
- **Data test:** a check that runs before writing and stops a bad write.
- **Majority vote** for messy labels.
- **Hash ID** (`txn_id`): a stable unique key computed from content.
- **Reconciliation:** comparing your numbers with an independent source.
