# Phase 3: agent v1

Status (31 Oct 2026): steps 0–8 done. Eval score **47/55 → 53/55 (96%)** after fixing the prompt and the semantic layer; the last misses are 1–2 flaky questions. Next: step 9, the Genie baseline (the gate). Full details of every step: `phase-3-build-log.md` in this folder.

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
6. `evals/golden.yml`: 55 questions in 7 groups (months, terms, categories, items, time patterns, money in, traps), each with a reference SQL query and a type (`value`, `no_data`, `refuse`). Answers are computed from the reference SQL, never stored in the repo; a private, git-ignored copy (`evals/golden-answers.private.md`) is for checking by hand. Checked by recomputing every answer from silver with separate SQL: 53/53 match. **Done.**
7. `src/analyst_agent/agent/evals.py` + `uv run eval`: runs every reference query and the agent, scores each answer (`value`: every expected number, label and date present; `no_data`: says so, never S$0; `refuse`: declines), prints pass/fail and a summary by level, saves the full report to `evals/runs/*.private.json` (git-ignored). 12 scoring tests. Baseline: **47/55**. **Done.**
8. Fix the agent from the eval failures, re-run the eval, keep only changes that raise the score. Only general rules, never answers for a specific golden question (overfitting).
   - 8a. Prompt: numbers only from SQL, answer the question as asked, describe the method truthfully, decline instead of substituting data. Scorer tightened (answer must be in the lead; refuse = no query) and `--rescore` added. 47 → **50/55**. **Done.**
   - 8b. Semantic layer: example questions replaced (they leaked golden questions), rules for category vs item, one-offs and money direction, per-month averages, a month without a year, lower-case item matching; the loop asks again after an empty reply; scorer accepts "no data (not S$0)". **53/55** in two full runs. **Done.**
9. Genie baseline: a Genie space on the same gold tables, the same questions, the same scoring. **The gate. Next.**
10. Simple chat UI.
11. Synthetic dataset for public demos: same schema and quirks, fake numbers and names.
12. Final check and wrap-up.

Commit status: steps 1–5 in `602447a`. Step 0 (docs) is in the next commit.

## Decisions

- **Model:** a free model hosted by Databricks, so data stays in Databricks. Qwen 3.5 122B by default; any endpoint with `--model`. Claude or Gemini later, behind the same `chat` function, with names replaced by tokens first.
- **The model never does arithmetic:** every number comes from `run_sql`.
- **Guardrails in code, not in the prompt:** `check_sql` rejects anything but one `SELECT` on gold before it reaches Databricks.
- **Tool errors go back to the model,** not to me, so it can fix its own query.
- **Written by hand first,** before LangGraph or Agent Bricks, to understand what those frameworks do.
- **Golden answers are computed, not stored** (step 6): the repo keeps each question's reference SQL; the expected answer is whatever it returns from gold at eval time. No real numbers in the public repo, and answers stay right as the data grows. A private copy (`*.private.*`, git-ignored) is for checking by hand.

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
