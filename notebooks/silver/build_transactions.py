# Databricks notebook source
# MAGIC %md
# MAGIC # Silver: build `transactions`
# MAGIC Rebuilds `silver.transactions` from the newest snapshot in `bronze.sheet_rows`.
# MAGIC The cleaning logic lives in `src/analyst_agent/transform/` (tested locally with `uv run pytest`);
# MAGIC this notebook only reads, runs the data tests and writes.
# MAGIC If any error-level test fails, the job fails and the previous good table is kept.

# COMMAND ----------

import sys

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("src_path", "")
catalog = dbutils.widgets.get("catalog")
src_path = dbutils.widgets.get("src_path")
if src_path and src_path not in sys.path:
    sys.path.insert(0, src_path)  # the bundle uploads the repo's src/ folder next to this notebook

from analyst_agent.transform.silver_transactions import build_silver, data_tests

source = f"{catalog}.bronze.sheet_rows"
target = f"{catalog}.silver.transactions"

# COMMAND ----------

silver = build_silver(spark.table(source))  # no .cache(): serverless doesn't support it
results = data_tests(silver)

for name, r in results.items():
    print(f"{r['severity']:5}  {name:30} {r['failures']}")

errors = [name for name, r in results.items() if r["severity"] == "error" and r["failures"]]
if errors:
    raise AssertionError(f"Data tests failed, {target} was not updated: {errors}")

# COMMAND ----------

(silver.write.mode("overwrite").option("overwriteSchema", "true")
    .option("comment", "One row per money movement, cleaned from the newest sheet snapshot. See README.")
    .saveAsTable(target))
print(f"Wrote {silver.count()} transactions to {target}")

# COMMAND ----------

display(spark.sql(f"""
  SELECT source_tab, txn_kind, count(*) AS txns, min(txn_date) AS first_date, max(txn_date) AS last_date
  FROM {target} GROUP BY ALL ORDER BY min(txn_date), txn_kind
"""))
