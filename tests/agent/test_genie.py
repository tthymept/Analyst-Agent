"""The Genie baseline's plumbing. No network."""

from enum import Enum
from types import SimpleNamespace as NS

from analyst_agent.agent.genie import answer_from, space_definition


class Status(Enum):
    COMPLETED = "COMPLETED"


def test_space_definition_lists_sorted_full_table_names_and_nothing_else():
    dataset = {"catalog": "cat", "schema": "gold", "tables": {"b_table": {}, "a_table": {}}}
    definition = space_definition(dataset)
    assert [t["identifier"] for t in definition["data_sources"]["tables"]] == ["cat.gold.a_table", "cat.gold.b_table"]
    assert "instructions" not in definition    # out of the box: none of our rules or examples


def test_answer_collects_text_sql_and_tables():
    message = NS(status=Status.COMPLETED, attachments=[
        NS(attachment_id="a1", text=None, query=NS(description="Totals by month", query="SELECT 1")),
        NS(attachment_id="a2", text=NS(content="You spent S$5."), query=None),
    ])
    answer = answer_from(message, lambda attachment_id: {"columns": ["x"], "rows": [[attachment_id]]})
    assert answer.status == "COMPLETED"
    assert answer.text == "Totals by month\n\nYou spent S$5."
    assert answer.sql == ["SELECT 1"]
    assert answer.tables == [{"columns": ["x"], "rows": [["a1"]]}]


def test_reply_without_attachments_is_empty_not_a_crash():
    answer = answer_from(NS(status=None, attachments=None), lambda _: {})
    assert (answer.status, answer.text, answer.sql) == ("UNKNOWN", "", [])
