"""Genie, the baseline to beat: Databricks' built-in assistant on the same gold tables.

    uv run genie setup            create the space (once; finds it by title after that)
    uv run genie ask "..."        ask it one question, showing its text, SQL and result table

The space is found by title, so its id never has to be written in this public repo.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

from analyst_agent.agent import tools

SPACE_TITLE = "Money baseline"
DATASET = "money"


@dataclass
class GenieAnswer:
    status: str
    text: str = ""                                      # what Genie wrote
    sql: list[str] = field(default_factory=list)        # every query it generated, in order
    tables: list[dict] = field(default_factory=list)    # each query's {columns, rows}


def space_definition(dataset: dict) -> dict:
    """The space's contents, in the format of Genie's serialized_space. Tables only: Genie out of the box."""
    tables = sorted(f"{dataset['catalog']}.{dataset['schema']}.{t}" for t in dataset["tables"])  # the API wants them sorted
    return {"version": 2, "data_sources": {"tables": [{"identifier": t} for t in tables]}}


def find_space() -> str | None:
    response = tools._client().genie.list_spaces()
    return next((s.space_id for s in response.spaces or [] if s.title == SPACE_TITLE), None)


def setup_space() -> tuple[str, bool]:
    """(space id, whether it was created now). Running it twice doesn't make a second space."""
    existing = find_space()
    if existing:
        return existing, False
    space = tools._client().genie.create_space(
        warehouse_id=tools._warehouse_id(),
        serialized_space=json.dumps(space_definition(tools.load_dataset(DATASET))),
        title=SPACE_TITLE,
        description="Baseline for the analyst agent's eval: the same gold tables, the same questions.",
    )
    return space.space_id, True


def answer_from(message, fetch_table) -> GenieAnswer:
    """Turn Genie's reply into plain text, SQL and tables. A reply is a list of attachments: text or a query.
    `fetch_table(attachment_id)` gets a query's result, which isn't part of the message."""
    answer = GenieAnswer(status=message.status.value if message.status else "UNKNOWN")
    texts = []
    for attachment in message.attachments or []:
        if attachment.text and attachment.text.content:
            texts.append(attachment.text.content)
        if attachment.query:
            if attachment.query.description:
                texts.append(attachment.query.description)
            answer.sql.append(attachment.query.query)
            answer.tables.append(fetch_table(attachment.attachment_id))
    answer.text = "\n\n".join(texts)
    return answer


def ask_genie(space_id: str, question: str) -> GenieAnswer:
    """One question in a new conversation, so each one starts fresh, like the agent."""
    genie = tools._client().genie
    message = genie.start_conversation_and_wait(space_id, question)

    def fetch_table(attachment_id: str) -> dict:
        result = genie.get_message_attachment_query_result(
            space_id, message.conversation_id, message.message_id, attachment_id).statement_response
        columns = [c.name for c in result.manifest.schema.columns]
        return {"columns": columns, "rows": (result.result.data_array if result.result else None) or []}

    return answer_from(message, fetch_table)


def main() -> None:
    parser = argparse.ArgumentParser(description="The Genie baseline.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="create the Genie space if it doesn't exist")
    ask = commands.add_parser("ask", help="ask Genie one question")
    ask.add_argument("question", nargs="+")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if args.command == "setup":
        _, created = setup_space()
        print(f"Space '{SPACE_TITLE}' {'created' if created else 'already exists'}.")
        return

    space_id = find_space()
    if not space_id:
        sys.exit(f"No space '{SPACE_TITLE}' yet. Run: uv run genie setup")
    answer = ask_genie(space_id, " ".join(args.question))
    print(f"status: {answer.status}\n\n{answer.text}")
    for sql, table in zip(answer.sql, answer.tables):
        print(f"\n-- query\n{sql}\n\n{table['columns']}\n{table['rows'][:20]}")


if __name__ == "__main__":
    main()
