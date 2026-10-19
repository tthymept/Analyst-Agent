# Databricks notebook source
# MAGIC %md
# MAGIC # Bronze: load Google Sheet snapshots
# MAGIC Reads every snapshot JSON in the Volume that is not yet in `bronze.sheet_rows` and appends it,
# MAGIC one row per sheet row, with the cells kept as strings exactly as fetched. No cleaning here.
# MAGIC Re-running is safe: snapshots already loaded are skipped (idempotent).

# COMMAND ----------

import json
import os

from pyspark.sql import types as T

dbutils.widgets.text("catalog", "workspace")
catalog = dbutils.widgets.get("catalog")
volume_dir = f"/Volumes/{catalog}/bronze/raw/google_sheets"
target = f"{catalog}.bronze.sheet_rows"

# COMMAND ----------

schema = T.StructType([
    T.StructField("snapshot_file", T.StringType(), False),
    T.StructField("snapshot_ts", T.StringType(), False),
    T.StructField("tab", T.StringType(), False),
    T.StructField("tab_index", T.IntegerType(), False),
    T.StructField("row_num", T.IntegerType(), False),   # 1 = the header row, same as in the sheet
    T.StructField("raw", T.ArrayType(T.StringType()), True),
    T.StructField("formatted", T.ArrayType(T.StringType()), True),
    T.StructField("formula", T.ArrayType(T.StringType()), True),
])


def cells(row):
    """Keep each cell as a JSON string so numbers, text and blanks survive the trip unchanged."""
    return [json.dumps(c, ensure_ascii=False) for c in row]


def flatten(file_name, snap):
    for tab in snap["tabs"]:
        raw, fmt, fx = tab.get("raw", []), tab.get("formatted", []), tab.get("formula", [])
        for i in range(max(len(raw), len(fmt), len(fx))):
            yield (
                file_name, snap["snapshot_ts"], tab["title"], tab["index"], i + 1,
                cells(raw[i]) if i < len(raw) else None,
                cells(fmt[i]) if i < len(fmt) else None,
                cells(fx[i]) if i < len(fx) else None,
            )

# COMMAND ----------

spark.sql(f"""
  CREATE TABLE IF NOT EXISTS {target} (
    snapshot_file STRING, snapshot_ts STRING, tab STRING, tab_index INT, row_num INT,
    raw ARRAY<STRING>, formatted ARRAY<STRING>, formula ARRAY<STRING>
  ) COMMENT 'Raw Google Sheet rows, one snapshot per ingest run. Never edited; clean in silver.'
""")

if not os.path.isdir(volume_dir):
    dbutils.notebook.exit(f"No snapshots yet: {volume_dir} does not exist. Run the ingest workflow first.")

loaded = {r.snapshot_file for r in spark.table(target).select("snapshot_file").distinct().collect()}
files = sorted(f for f in os.listdir(volume_dir) if f.endswith(".json"))
new_files = [f for f in files if f not in loaded]
print(f"{len(files)} snapshot files in the Volume, {len(new_files)} new")

for name in new_files:
    with open(f"{volume_dir}/{name}", encoding="utf-8") as fh:
        snap = json.load(fh)
    rows = list(flatten(name, snap))
    spark.createDataFrame(rows, schema).write.mode("append").saveAsTable(target)
    print(f"Loaded {name}: {len(rows)} rows from {len(snap['tabs'])} tabs")

# COMMAND ----------

display(spark.sql(f"""
  SELECT snapshot_file, count(DISTINCT tab) AS tabs, count(*) AS rows
  FROM {target} GROUP BY snapshot_file ORDER BY snapshot_file DESC
"""))
