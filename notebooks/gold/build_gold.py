# Databricks notebook source
# MAGIC %md
# MAGIC # Gold: facts, dates and metrics
# MAGIC Rebuilds the gold tables from `silver.transactions`. The dashboard and the agent read only from here.
# MAGIC The logic lives in `src/analyst_agent/transform/gold.py` (tested locally with `uv run pytest`);
# MAGIC this notebook only reads, runs the tests and writes. If a test fails, the previous tables are kept.

# COMMAND ----------

import sys

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("src_path", "")
catalog = dbutils.widgets.get("catalog")
src_path = dbutils.widgets.get("src_path")
if src_path and src_path not in sys.path:
    sys.path.insert(0, src_path)  # the bundle uploads the repo's src/ folder next to this notebook

from analyst_agent.transform.gold import daily_living_spend, dim_date, fact_transaction, gold_tests, monthly_summary

silver = spark.table(f"{catalog}.silver.transactions")

# COMMAND ----------

fact = fact_transaction(silver)
dates = dim_date(silver)
daily = daily_living_spend(fact, dates)
monthly = monthly_summary(fact, dates)

results = gold_tests(fact, daily, monthly)
for name, r in results.items():
    print(f"{r['severity']:5}  {name:30} {r['failures']}")
errors = [name for name, r in results.items() if r["severity"] == "error" and r["failures"]]
if errors:
    raise AssertionError(f"Gold tests failed, gold tables were not updated: {errors}")

# COMMAND ----------

tables = {
    "fact_transaction": (fact, "One row per money movement, with is_living_spend and month. See README."),
    "dim_date": (dates, "One row per day, Jul 2025 to May 2029: study year, semester, term, is_tracked."),
    "daily_living_spend": (daily, "Living spend per tracked day, S$0 on days with no purchases."),
    "monthly_summary": (monthly, "Per calendar month: living spend, one-offs, reimbursable, funding, income."),
}
for name, (df, comment) in tables.items():
    (df.write.mode("overwrite").option("overwriteSchema", "true").option("comment", comment)
        .saveAsTable(f"{catalog}.gold.{name}"))
    print(f"Wrote {catalog}.gold.{name}")

# COMMAND ----------

display(spark.table(f"{catalog}.gold.monthly_summary").orderBy("month"))
