# Phase 3: agent v1

Status (30 Oct 2026): steps 0–5 done. The agent answers questions from the terminal (`uv run ask "..."`) and got 4 of 4 test questions right. Next: step 6, the golden question set. Full details of every step: `phase-3-build-log.md` in this folder.

## What Phase 3 builds

An analyst agent: I ask a question in plain English, it writes SQL against gold, the warehouse computes the answer, and it explains the result with what it includes or leaves out.

```
"How much did I spend in March?"
  │
loop.py ──► model (Databricks-hosted) ── "call describe_dataset('money')"
  │            ▲            │
  │            │            └─ "call run_sql('SELECT ... FROM monthly_summary ...')"
  │            │                       │
  │      tool results         tools.py: check_sql (one SELECT, gold only, LIMIT) → warehouse
  ▼
"S$… in March 2026 (excludes tuition and hall fees)."
```

- **Semantic layer** (`semantic/money.yml`): what every gold table, column and metric means, the rules, and example SQL.
- **Tools** (`agent/tools.py`): `list_datasets`, `describe_dataset`, `run_sql`, with SQL guardrails.
- **Loop** (`agent/loop.py`): hand-written tool calling, the model behind one `chat` function.
- **Evals:** a golden question set and a scorer, compared with Databricks Genie.

Done when (from the plan): **the agent beats Genie on the golden question set.**

## Steps

Built one step at a time from step 6 on: Claude builds and explains, I test, then say "next".

0. Housekeeping: `CLAUDE.md` (rules and pace), docs in `uni-copilot`'s format (`docs/`). **Done.**
1. `semantic/money.yml`: the 4 gold tables, their columns, the rules ("spending" = living spend; no data ≠ S$0; per-day divides by tracked days), metric definitions and 4 example question → SQL pairs. **Done.**
2. `src/analyst_agent/agent/tools.py`: `list_datasets`, `describe_dataset`, `run_sql`; `check_sql` parses the SQL with `sqlglot`: one statement, `SELECT` only, gold tables only, `LIMIT 200` added, 30 s timeout. **Done.**
3. `src/analyst_agent/agent/loop.py` + `cli.py`: the tool-calling loop (max 8 steps), tool errors returned to the model, `uv run ask "..." [--sql] [--model ...]`. **Done.**
4. `tests/agent/test_agent.py`: guardrail tests (allowed and rejected queries) and loop tests with a scripted fake model, no network. **Done.**
5. Model comparison on real data: 5 hosted models, 4 questions, answers checked by hand. Default: Qwen 3.5 122B. **Done.**
6. Golden question set: 40–60 questions (easy, multi-step, ambiguous, trick), each with an expected answer computed by hand in SQL. Stored without real numbers in the repo (see Decisions). **Next.**
7. Eval runner: run the agent on every question, score each answer, record tool calls and time; a report I can compare between runs.
8. Genie baseline: a Genie space on the same gold tables, the same questions, the same scoring. **The gate.**
9. Simple chat UI.
10. Synthetic dataset for public demos: same schema and quirks, fake numbers and names.
11. Final check and wrap-up.

Commit status: steps 1–5 in `602447a`. Step 0 (docs) is in the next commit.

## Decisions

- **Model:** a free model hosted by Databricks, so data stays in Databricks. Qwen 3.5 122B by default; any endpoint with `--model`. Claude or Gemini later, behind the same `chat` function, with names replaced by tokens first.
- **The model never does arithmetic:** every number comes from `run_sql`.
- **Guardrails in code, not in the prompt:** `check_sql` rejects anything but one `SELECT` on gold before it reaches Databricks.
- **Tool errors go back to the model,** not to me, so it can fix its own query.
- **Written by hand first,** before LangGraph or Agent Bricks, to understand what those frameworks do.
- **Golden answers stay private:** the expected numbers are real data, so they can't go in the public repo. Decided in step 6.

## Things to check during Phase 3

- **Agent identity:** `run_sql` runs with my own Databricks login. A read-only identity scoped to gold would be stronger. Not verified whether Free Edition supports one.
- **Genie on Free Edition:** whether a Genie space can be created and queried in a way we can score.
- **Answer stability:** with `temperature = 0` the same question should give the same SQL; not yet measured over many runs.
- **Names in item text:** the model sees item names (some contain friends' names) inside Databricks. Fine for a Databricks-hosted model; must be tokenised before any outside model.

## New ideas you'll meet

- **Tool calling / function calling:** the model asks for a function, your code runs it.
- **Agent loop (ReAct):** think → act (tool) → observe → repeat → answer.
- **Text-to-SQL** and the **semantic layer**.
- **Guardrails** and **SQL parsing** (an abstract syntax tree).
- **Fake / scripted model** for tests.
- **Evals, golden set, execution accuracy, baseline.**
