# Phase 3 build log

What each finished step changed, how to test it, and its commit. Steps 1–5 were built on 30 Oct 2026; from step 6 on, each step is built one at a time and logged here when it's done. Unverified points are in "Open items"; terms like "tool calling" are in "Concepts".

## How it all connects (read this first when you come back)

### The layers

```
You ("how much did I spend in March?")
  │   uv run ask "..."                                   agent/cli.py
  ▼
ask(question, chat)                                      agent/loop.py
  │   keeps the conversation (messages), runs tools, stops at an answer or 8 steps
  ├──► chat(messages, TOOL_SPECS) ── the model ─────────  databricks_chat: POST /serving-endpoints/<model>/invocations
  │
  └──► call_tool(name, args)
         ├─ list_datasets / describe_dataset ─► semantic/money.yml        agent/tools.py
         └─ run_sql(sql) ─► check_sql (sqlglot) ─► SQL warehouse ─► gold.*
```

Each layer knows only the one below. `loop.py` doesn't know SQL exists; it only passes messages and tool results. `tools.py` doesn't know a model exists; it reads YAML and runs guarded SQL. The model is one function, so it can be swapped.

### One question, end to end: "How much did I spend in March 2026?"

1. **`ask` starts the conversation:** a system message (how to work: read the semantic layer first, every number from SQL, say "no data" instead of S$0) and the question.
2. **The model asks for a tool:** its reply has no text, only `tool_calls: describe_dataset(name="money")`.
3. **`call_tool` runs it:** `tools.describe_dataset("money")` returns `semantic/money.yml` as text. The result is added to the conversation as a `tool` message.
4. **The model writes SQL:** from the rules ("spending = living spend") and the tables, it calls `run_sql("SELECT living_spend, tracked_days FROM monthly_summary WHERE month = DATE'2026-03-01'")`.
5. **`run_sql` guards and runs it:** `check_sql` parses it (one statement, a `SELECT`, only gold tables) and adds `LIMIT 200`; the Databricks SDK sends it to the Serverless Starter Warehouse with catalog `workspace` and schema `gold`; the rows come back as JSON.
6. **The model answers:** no tool calls this time, just text: the number in S$ and what it leaves out. `ask` returns it with the SQL that ran.

Short version:

```
question → model: describe_dataset → YAML → model: run_sql(SQL) → check_sql → warehouse → rows → model: answer
```

## Step 0: housekeeping

- **`CLAUDE.md`:** project rules every Claude Code session reads: environment, commands, architecture rules, non-negotiables (no real data in the repo or docs; the agent only reads; data stays in Databricks; ask before deploying or running jobs), the pace (one step at a time when building), and Conventional Commits.
- **`docs/`:** the plan and one folder per phase (core + build log), in the format of the `uni-copilot` repo. Written without real amounts or names, because the repo is public. The old local `Project Plan.md` stays local.

Check: `git status` shows `CLAUDE.md` and `docs/` as new.

Commit, short version:

```
git add CLAUDE.md docs
git commit -m "docs: add CLAUDE.md, project plan and phase 0-3 docs"
```

Commit with a body:

```
git add CLAUDE.md docs
git commit -m "docs: add CLAUDE.md, project plan and phase 0-3 docs" -m "- CLAUDE.md: rules, commands, pace and commit style
- docs/analyst-agent-project-plan.md: goals, architecture, rules, roadmap, verified facts
- docs/phase 0-3: core overview and build log per phase
- no real amounts or names (public repo)"
```

## Step 1: semantic layer (`semantic/money.yml`)

The agent's guide to the data. Sections:

- `description`: what the dataset is, the currency, and when there's no data (June, December, most of July).
- `rules`: "spending" means living spend; tuition and work money are still answerable when asked; no data ≠ S$0; per-day divides by tracked days; how terms are named; match items on `item_clean` with `LIKE`.
- `tables`: each gold table and its columns, in words.
- `metrics`: `living_spend`, `living_spend_per_day`, with SQL.
- `examples`: 4 question → SQL pairs (a month, two terms compared, an item, tuition).

YAML gotcha met here: a value containing `: ` must be quoted, or YAML reads it as a nested key.

## Step 2: tools and guardrails (`agent/tools.py`)

- **`list_datasets`, `describe_dataset`:** read every `semantic/*.yml`.
- **`check_sql(sql, dataset)`:** parses with `sqlglot` (dialect `databricks`) into a tree, then:
  - exactly one statement;
  - the statement is a query (`SELECT`, `UNION`, `WITH ... SELECT`);
  - no `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE`, `DROP`, `ALTER`, `SET`, `USE`, `COPY` anywhere in the tree;
  - every table is one of the dataset's tables (names defined by a `WITH` are allowed), in catalog `workspace` and schema `gold` if qualified;
  - `LIMIT 200` added when there's no `LIMIT`.
  A broken rule raises `SqlRejected` with a message written for the model ("Use only: …").
- **`run_sql`:** `check_sql`, then the Databricks SDK's Statement Execution API (`wait_timeout` 30 s, `row_limit` 200). Returns `{sql, columns, rows, truncated}` or `{sql, error}`. The warehouse is found by name (or `DATABRICKS_WAREHOUSE_ID`).
- New dependencies: `sqlglot` (SQL parser) and `pyyaml`, added with `uv add`.

## Step 3: the loop and the command (`agent/loop.py`, `agent/cli.py`)

- **`TOOL_SPECS`:** each tool's name, description and JSON-schema inputs: what the model sees (never the code).
- **`databricks_chat(model)`:** returns a `chat(messages, specs)` function that calls a Databricks serving endpoint (OpenAI-compatible format). Swapping providers = writing another function with the same shape.
- **`ask(question, chat, run_tool)`:** up to 8 rounds of "model replies → run its tool calls → add results". Stops when the model replies with text and no tool calls. Collects every query that ran successfully.
- **`call_tool`:** tool mistakes (an unknown dataset, a rejected query, a SQL error) go back to the model as `{"error": ...}`; found when the model called `describe_dataset` with a table name and the first version crashed.
- **`text_of`:** some models reply with a list of parts (reasoning + text); only the text is kept.
- **`cli.py`:** `uv run ask "..."`, `--sql` prints the queries, `--model` picks an endpoint; output forced to UTF-8 because the Windows console can't print some characters models use.
- `pyproject.toml`: new command `ask = "analyst_agent.agent.cli:main"`.

Test (PowerShell, in the project folder; needs the Databricks CLI login):

```powershell
uv run ask --sql "How much did I spend in March 2026?"
```

Expect: one sentence with an amount in S$ and what it excludes; the SQL reads `monthly_summary`; 3–5 steps.

## Step 4: tests (`tests/agent/test_agent.py`)

No network, no Databricks:

- **Semantic layer:** `money` is listed; every example's SQL passes the guardrails.
- **Allowed:** a plain `SELECT`, a fully qualified gold table, a `WITH`, a join.
- **Rejected:** `DROP`, `DELETE`, `INSERT`, two statements, silver, bronze, a system table, an unknown table, broken SQL.
- **`LIMIT`:** added when missing, kept when present.
- **A rejected query never reaches Databricks.**
- **Loop:** a scripted fake model calls `describe_dataset`, a failing `run_sql`, then a working one, then answers; the test checks the answer, that only the working SQL is reported, and that results and errors were passed back to the model. A model that never stops is cut at the step limit.

```powershell
uv run pytest
```

Expect all tests to pass (37 in total at this point).

## Step 5: model comparison

The same questions on 5 Databricks-hosted models, answers checked against SQL I wrote myself (real data, read-only, 30 Oct). Results:

| Model | Result |
| --- | --- |
| Qwen 3.5 122B | 4 of 4 correct, clearest answers. **Default.** |
| GPT-OSS 120B | 4 of 4 correct |
| Llama 4 Maverick | Numbers correct; once said "S$0" for a month with no data (breaks a rule) |
| Llama 3.3 70B | Wrong on the first question (filtered on `date_fix`, a cleaning note, as if it were a date) |
| DeepSeek V4 Flash | Endpoint retired |

Questions: one month's spend, this term vs last term per day, an item with several spellings (checked: the agent's `LIKE` found every spelling of that item and none of the different items with similar names), and a month with no data.

Lesson: an agent that runs isn't an agent that's right. That's what the golden question set (step 6) is for.

Commit (steps 1–5): `602447a` "Analyst agent v1: semantic layer, guarded run_sql, tool loop".

## Open items

- **Read-only identity:** `run_sql` uses my own login; the guardrail is the SQL parser only.
- **Genie on Free Edition:** not yet tried.
- **Answer stability over many runs:** not measured.
- **Questions across items with free-text names** ("public transport") need `search_values` (Phase 4).
- **Two money-in rows** treated as paybacks whose meaning I haven't confirmed.

## Concepts

- **LLM (large language model):** the model that reads the conversation and writes the next message.
- **Tool calling (function calling):** the model replies with "call this function with these arguments" instead of text; your code runs it and sends the result back.
- **Tool spec:** a tool's name, description and input schema, which is all the model knows about it.
- **Agent loop / ReAct:** reason → act (call a tool) → observe (read the result) → repeat → answer.
- **System prompt:** the first message, with standing instructions for the model.
- **Message roles:** `system`, `user`, `assistant`, `tool`.
- **Text-to-SQL:** turning a question into a SQL query.
- **Semantic layer:** the meaning of the data (definitions, rules, examples), kept apart from the data itself.
- **Guardrail:** a check in code that limits what the agent can do, whatever the model says.
- **Parser / AST (abstract syntax tree):** `sqlglot` turns SQL text into a tree of parts (statement, tables, limit), so rules can be checked reliably instead of with text matching.
- **Serving endpoint / Foundation Model API:** a model hosted by Databricks, called over HTTP.
- **OpenAI-compatible format:** a common shape for chat requests and replies (`messages`, `tools`, `choices[0].message`).
- **Temperature:** randomness in the model's choices; 0 = as repeatable as possible.
- **Fake / scripted model:** a test stand-in that returns fixed replies, so the loop is tested without a network.
- **Eval / golden set:** questions with known correct answers, used to score the agent after every change.
- **Baseline:** what you compare against (Genie).
- **Execution accuracy:** whether the answer's numbers match the expected ones, regardless of how the SQL was written.
