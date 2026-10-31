"""The analyst agent: a hand-written tool-calling loop.

    question -> LLM -> tool call(s) -> results back to the LLM -> ... -> final answer

The LLM decides what to call; this file only runs the tools and keeps the transcript. The model sits
behind `chat`, one function taking OpenAI-style messages + tools, so a different provider (Claude, a
local model) is one new function. The default is a free model hosted by Databricks, so the data never
leaves Databricks.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Callable

from analyst_agent.agent import tools

DEFAULT_MODEL = "databricks-qwen35-122b-a10b"  # best of the free Databricks models on our first questions
MAX_STEPS = 8

SYSTEM_PROMPT = """You are a careful data analyst answering questions about the user's own data.

How to work:
1. Call describe_dataset first to learn the tables, columns and rules. Follow its rules exactly.
2. Every number in your answer must be copied exactly from a run_sql result. Never add, subtract,
   divide or adjust a number yourself, not even "to remove" something: if the answer needs a new number
   (a difference, a share, an average, a total without some part), write SQL that returns it.
3. If a query fails, read the error, fix the SQL and try again.
4. Answer the question exactly as asked: the same grouping and comparison it uses. "First half or
   second half of the month" means one number for each half, not one per day.
5. Describe your method truthfully: say what you filtered on and what you divided by, exactly as your
   SQL did. Never claim a method your SQL didn't use.
6. You can only read the tables that describe_dataset lists. If asked for anything else (other tables
   or schemas, raw or unprocessed data, or to change or delete data), say you can't do that and stop.
   Don't offer other data instead.
7. Reply briefly in plain words: the answer with its number and unit (S$), then one line on what it
   includes or leaves out (for example: "excludes tuition and hall fees"). If the period has no data,
   say so; do not report S$0.
"""

TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "list_datasets",
        "description": "List the datasets you can query, with a one-line description of each.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "describe_dataset",
        "description": "The dataset's semantic layer: tables, columns, rules, metric definitions and example SQL.",
        "parameters": {"type": "object", "properties": {"name": {
            "type": "string", "description": "A dataset name from list_datasets, e.g. 'money' (not a table name)"}},
            "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "run_sql",
        "description": "Run one read-only SELECT (Databricks SQL) on the dataset's tables. Returns columns and rows, "
                       f"at most {tools.MAX_ROWS}, or an error to fix.",
        "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}}},
]

Chat = Callable[[list[dict], list[dict]], dict]  # (messages, tool specs) -> one assistant message


@dataclass
class Answer:
    text: str
    sql: list[str] = field(default_factory=list)   # every query that ran, in order
    steps: int = 0


def databricks_chat(model: str | None = None) -> Chat:
    """A model served by Databricks (Foundation Model API, OpenAI-compatible)."""
    endpoint = model or os.environ.get("ANALYST_MODEL", DEFAULT_MODEL)

    def chat(messages: list[dict], specs: list[dict]) -> dict:
        body = {"messages": messages, "tools": specs, "max_tokens": 1500, "temperature": 0}
        response = tools._client().api_client.do("POST", f"/serving-endpoints/{endpoint}/invocations", body=body)
        return response["choices"][0]["message"]

    return chat


def text_of(content) -> str:
    """Message content as plain text. Some models send a list of parts (reasoning + text); keep the text."""
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return content or ""


def call_tool(name: str, args: dict) -> dict | list | str:
    """Run one tool. A mistake (unknown dataset, bad SQL) comes back as {"error"} for the model to fix."""
    try:
        if name == "list_datasets":
            return tools.list_datasets()
        if name == "describe_dataset":
            return tools.describe_dataset(args.get("name", "money"))
        if name == "run_sql":
            return tools.run_sql(args.get("sql", ""))
        return {"error": f"Unknown tool '{name}'."}
    except KeyError as e:
        return {"error": e.args[0]}


def ask(question: str, chat: Chat | None = None, run_tool: Callable = call_tool) -> Answer:
    chat = chat or databricks_chat()
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}]
    answer = Answer(text="")

    for answer.steps in range(1, MAX_STEPS + 1):
        message = chat(messages, TOOL_SPECS)
        calls = message.get("tool_calls") or []
        content = text_of(message.get("content"))
        messages.append({"role": "assistant", "content": content, "tool_calls": calls} if calls
                        else {"role": "assistant", "content": content})
        if not calls:
            if not content.strip():  # some models stop with an empty reply; ask once more for the answer
                messages.append({"role": "user", "content": "You haven't written an answer yet. "
                                 "Answer the question now, using the query results above."})
                continue
            answer.text = content.strip()
            return answer

        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = run_tool(name, args)
            if name == "run_sql" and isinstance(result, dict) and "error" not in result:
                answer.sql.append(result["sql"])
            content = result if isinstance(result, str) else json.dumps(result, default=str)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})

    answer.text = "I couldn't finish within the step limit. Try asking a narrower question."
    return answer
