"""The Genie baseline's plumbing. No network."""

from enum import Enum
from types import SimpleNamespace as NS

from analyst_agent.agent.evals import load_golden
from analyst_agent.agent.genie import answer_from, qualified, space_definition
from analyst_agent.agent.tools import load_dataset


class Status(Enum):
    COMPLETED = "COMPLETED"


class Purpose(Enum):
    ANSWER = "TEXT_ATTACHMENT_PURPOSE_ANSWER"
    OTHER = "TEXT_ATTACHMENT_PURPOSE_OTHER"


def test_space_definition_lists_sorted_full_table_names_and_nothing_else():
    dataset = {"catalog": "cat", "schema": "gold", "tables": {"b_table": {}, "a_table": {}}}
    definition = space_definition(dataset)
    assert [t["identifier"] for t in definition["data_sources"]["tables"]] == ["cat.gold.a_table", "cat.gold.b_table"]
    assert "instructions" not in definition    # out of the box: none of our rules or examples


def test_knowledge_holds_the_whole_semantic_layer_and_no_golden_question():
    dataset = load_dataset("money")
    instructions = space_definition(dataset, knowledge=True)["instructions"]
    text = instructions["text_instructions"][0]["content"][0]
    assert all(rule.strip() in text for rule in dataset["rules"])
    assert all(f"Table {t}:" in text for t in dataset["tables"]) and "item_clean" in text and "living_spend:" in text

    examples = instructions["example_question_sqls"]
    assert len(examples) == len(dataset["examples"])
    assert [e["id"] for e in examples] == sorted(e["id"] for e in examples)
    assert not {q for e in examples for q in e["question"]} & {q["question"] for q in load_golden()}  # no leakage


def test_examples_get_full_table_names():
    dataset = {"catalog": "cat", "schema": "gold"}
    sql = qualified("SELECT SUM(f.x) FROM fact f JOIN dim d ON d.a = f.a", dataset)
    assert "cat.gold.fact AS f" in sql and "cat.gold.dim AS d" in sql


def test_answer_is_the_written_answer_with_its_sql_and_tables():
    message = NS(status=Status.COMPLETED, attachments=[
        NS(attachment_id="a1", text=None, query=NS(description="You want the totals", query="SELECT 1")),
        NS(attachment_id="a2", text=None, query=None),   # suggested questions, a chart
        NS(attachment_id="a3", text=NS(content="You spent S$5.", purpose=Purpose.ANSWER), query=None),
        NS(attachment_id="a4", text=NS(content="Anything else?", purpose=Purpose.OTHER), query=None),
    ])
    answer = answer_from(message, lambda attachment_id: {"columns": ["x"], "rows": [[attachment_id]]})
    assert answer.status == "COMPLETED"
    assert answer.text == "You spent S$5."
    assert answer.sql == ["SELECT 1"]
    assert answer.tables == [{"columns": ["x"], "rows": [["a1"]]}]


def test_reply_without_attachments_is_empty_not_a_crash():
    answer = answer_from(NS(status=None, attachments=None), lambda _: {})
    assert (answer.status, answer.text, answer.sql) == ("UNKNOWN", "", [])
