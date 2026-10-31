"""The agent's guardrails and loop, with no network: a scripted fake model and fake tools."""

import json

import pytest

from analyst_agent.agent import tools
from analyst_agent.agent.loop import ask

MONEY = tools.load_dataset("money")


def check(sql):
    return tools.check_sql(sql, MONEY)


# ---------------------------------------------------------------------------- semantic layer

def test_semantic_layer_lists_money():
    assert [d["name"] for d in tools.list_datasets()] == ["money"]
    assert "living_amount" in tools.describe_dataset("money")


def test_semantic_examples_pass_the_guardrails():
    for example in MONEY["examples"]:
        check(example["sql"])


# ---------------------------------------------------------------------------- SQL guardrails

@pytest.mark.parametrize("sql", [
    "SELECT living_spend FROM monthly_summary WHERE month = DATE'2026-03-01'",
    "SELECT * FROM workspace.gold.fact_transaction",
    "WITH t AS (SELECT term, SUM(living_spend) s FROM monthly_summary GROUP BY term) SELECT * FROM t",
    "SELECT d.date FROM dim_date d JOIN daily_living_spend s ON d.date = s.date",
])
def test_allowed_queries(sql):
    check(sql)


@pytest.mark.parametrize("sql, reason", [
    ("DROP TABLE monthly_summary", "Only SELECT"),
    ("DELETE FROM fact_transaction", "Only SELECT"),
    ("INSERT INTO dim_date SELECT * FROM dim_date", "Only SELECT"),
    ("SELECT 1; DROP TABLE dim_date", "exactly one"),
    ("SELECT * FROM workspace.silver.transactions", "not allowed"),
    ("SELECT * FROM workspace.bronze.sheet_rows", "not allowed"),
    ("SELECT * FROM system.billing.usage", "not allowed"),
    ("SELECT * FROM secrets", "Unknown table"),
    ("SELEC living_spend FROM", "Only SELECT|parse"),
])
def test_rejected_queries(sql, reason):
    with pytest.raises(tools.SqlRejected, match=reason):
        check(sql)


def test_limit_added_only_when_missing():
    assert "LIMIT 200" in check("SELECT * FROM fact_transaction")
    assert "LIMIT 5" in check("SELECT * FROM fact_transaction LIMIT 5")


def test_rejected_query_never_reaches_databricks():
    result = tools.run_sql("DROP TABLE monthly_summary")       # would fail on _client() if it got that far
    assert "error" in result and "Only SELECT" in result["error"]


# ---------------------------------------------------------------------------- loop

def tool_call(i, tool, **args):
    return {"id": f"call_{i}", "type": "function", "function": {"name": tool, "arguments": json.dumps(args)}}


def scripted(*replies):
    """A fake model that returns the given assistant messages in order and records what it was sent."""
    sent = []

    def chat(messages, specs):
        sent.append([dict(m) for m in messages])
        return replies[len(sent) - 1]
    chat.sent = sent
    return chat


def fake_tools(name, args):
    if name == "describe_dataset":
        return "tables: monthly_summary"
    if name == "run_sql" and "bad" in args["sql"]:
        return {"sql": args["sql"], "error": "UNRESOLVED_COLUMN"}
    return {"sql": args["sql"], "columns": ["living_spend"], "rows": [["720.81"]], "truncated": False}


def test_loop_runs_tools_and_returns_answer():
    chat = scripted(
        {"content": "", "tool_calls": [tool_call(1, "describe_dataset", name="money")]},
        {"content": "", "tool_calls": [tool_call(2, "run_sql", sql="SELECT bad FROM monthly_summary")]},
        {"content": "", "tool_calls": [tool_call(3, "run_sql", sql="SELECT living_spend FROM monthly_summary")]},
        {"content": "You spent S$720.81 in March 2026."},
    )
    answer = ask("How much did I spend in March 2026?", chat=chat, run_tool=fake_tools)

    assert answer.text == "You spent S$720.81 in March 2026."
    assert answer.sql == ["SELECT living_spend FROM monthly_summary"]      # the failed query isn't reported
    assert answer.steps == 4
    last = chat.sent[-1]
    assert last[-1]["role"] == "tool" and "720.81" in last[-1]["content"]  # results go back to the model
    assert any("UNRESOLVED_COLUMN" in m["content"] for m in last if m["role"] == "tool")


def test_loop_asks_again_after_an_empty_reply():
    chat = scripted(
        {"content": "", "tool_calls": [tool_call(1, "run_sql", sql="SELECT living_spend FROM monthly_summary")]},
        {"content": ""},                                                     # the model stops without answering
        {"content": "You spent S$720.81."},
    )
    answer = ask("How much?", chat=chat, run_tool=fake_tools)
    assert answer.text == "You spent S$720.81."
    assert chat.sent[-1][-1]["role"] == "user" and "answer" in chat.sent[-1][-1]["content"]


def test_loop_stops_at_step_limit():
    looping = scripted(*[{"content": "", "tool_calls": [tool_call(i, "list_datasets")]} for i in range(20)])
    answer = ask("anything", chat=looping, run_tool=lambda name, args: [])
    assert "step limit" in answer.text
