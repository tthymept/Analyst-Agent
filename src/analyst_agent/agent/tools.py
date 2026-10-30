"""The agent's tools: read the semantic layer, and run read-only SQL on gold.

The LLM never sees the warehouse directly. Every query goes through `check_sql`, which parses it and
allows exactly one SELECT over the dataset's own tables, with a row limit. Anything else is rejected before
it reaches Databricks, and the error goes back to the LLM so it can fix its query.
"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path

import sqlglot
import yaml
from sqlglot import exp

SEMANTIC_DIR = Path(__file__).resolve().parents[3] / "semantic"
MAX_ROWS = 200
TIMEOUT = "30s"
WAREHOUSE_NAME = "Serverless Starter Warehouse"

# Statement types that are never allowed anywhere in a query, even nested.
FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command,
             exp.Use, exp.Set, exp.Copy, exp.TruncateTable)


class SqlRejected(ValueError):
    """The query broke a guardrail. The message is written for the LLM to act on."""


# ---------------------------------------------------------------------------- semantic layer

@cache
def load_dataset(name: str) -> dict:
    path = SEMANTIC_DIR / f"{name}.yml"
    if not path.is_file():
        raise KeyError(f"Unknown dataset '{name}'. Call list_datasets to see the options.")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def list_datasets() -> list[dict]:
    return [{"name": d["name"], "title": d["title"], "description": d["description"].strip()}
            for d in (load_dataset(p.stem) for p in sorted(SEMANTIC_DIR.glob("*.yml")))]


def describe_dataset(name: str) -> str:
    """The whole semantic file as YAML: tables, columns, rules, metrics and examples."""
    return yaml.safe_dump(load_dataset(name), sort_keys=False, allow_unicode=True, width=120)


# ---------------------------------------------------------------------------- SQL guardrails

def check_sql(sql: str, dataset: dict) -> str:
    """Return the query, safe to run, or raise SqlRejected saying what to change."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="databricks") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SqlRejected(f"Could not parse the SQL: {e}") from None
    if len(statements) != 1:
        raise SqlRejected("Send exactly one SQL statement.")
    query = statements[0]
    if not isinstance(query, exp.Query):
        raise SqlRejected("Only SELECT queries are allowed.")
    if any(isinstance(node, FORBIDDEN) for node in query.walk()):
        raise SqlRejected("Only SELECT queries are allowed: no INSERT, UPDATE, DELETE, CREATE, DROP or SET.")

    allowed = set(dataset["tables"])
    ctes = {cte.alias_or_name for cte in query.find_all(exp.CTE)}
    for table in query.find_all(exp.Table):
        if table.name in ctes and not table.db:
            continue
        if table.catalog and table.catalog != dataset["catalog"] or table.db and table.db != dataset["schema"]:
            raise SqlRejected(f"Table {table.sql()} is not allowed. Use only: {', '.join(sorted(allowed))}.")
        if table.name not in allowed:
            raise SqlRejected(f"Unknown table '{table.name}'. Use only: {', '.join(sorted(allowed))}.")

    if query.args.get("limit") is None:
        query = query.limit(MAX_ROWS)
    return query.sql(dialect="databricks")


# ---------------------------------------------------------------------------- run SQL

@cache
def _client():
    from databricks.sdk import WorkspaceClient  # imported here so the guardrail tests need no Databricks login
    return WorkspaceClient()


@cache
def _warehouse_id() -> str:
    if os.environ.get("DATABRICKS_WAREHOUSE_ID"):
        return os.environ["DATABRICKS_WAREHOUSE_ID"]
    for w in _client().warehouses.list():
        if w.name == WAREHOUSE_NAME:
            return w.id
    raise RuntimeError(f"No SQL warehouse named '{WAREHOUSE_NAME}'. Set DATABRICKS_WAREHOUSE_ID.")


def run_sql(sql: str, dataset_name: str = "money") -> dict:
    """Run one guarded SELECT. Returns {sql, columns, rows, truncated} or {sql, error}: errors are for the LLM."""
    dataset = load_dataset(dataset_name)
    try:
        safe = check_sql(sql, dataset)
    except SqlRejected as e:
        return {"sql": sql, "error": str(e)}

    r = _client().statement_execution.execute_statement(
        statement=safe, warehouse_id=_warehouse_id(), catalog=dataset["catalog"], schema=dataset["schema"],
        wait_timeout=TIMEOUT, row_limit=MAX_ROWS,
    )
    state = r.status.state.value if r.status and r.status.state else "UNKNOWN"
    if state != "SUCCEEDED":
        message = r.status.error.message if r.status and r.status.error else f"Query ended in state {state}."
        return {"sql": safe, "error": message}
    columns = [c.name for c in r.manifest.schema.columns]
    rows = (r.result.data_array if r.result else None) or []
    return {"sql": safe, "columns": columns, "rows": rows, "truncated": bool(r.manifest.truncated)}
