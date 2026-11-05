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

## Step 6: golden question set (`evals/golden.yml`)

55 questions the agent should get right, in 7 groups:

| Group | Questions | What it tests |
| --- | --- | --- |
| A. Months | q01–q10 | Simple lookups, highest/lowest month, totals, per-day over tracked days |
| B. Terms and years | q11–q18 | Term names, "this term vs last term", leaving out the July arrival days |
| C. Categories | q19–q26 | Category totals, shares, ranking, an ambiguous "per month" |
| D. Items | q27–q32 | Free-text item names matched with `LIKE` (MRT, Grab), most frequent, most expensive |
| E. Time patterns | q33–q38 | Weekend vs weekday, top day and week, days with no purchases, last 7 days |
| F. Money in, one-offs, work | q39–q46 | Tuition, hall fees, funding, work money, income, paybacks, corrections, "including tuition" |
| G. Traps | q47–q55 | No data (June, December, the future, an untracked day), a tracked S$0 day, a month without a year, an ambiguous "expenses", a delete request, a bronze request |

Levels: 19 easy, 21 medium, 6 hard, 9 trick. Types: 49 `value`, 4 `no_data`, 2 `refuse`.

Each question has:

- **`sql`:** a reference query written by hand and checked. It runs through the agent's own guardrails, so it uses only gold tables.
- **`type`:** `value` (every value in the reference result must appear in the answer), `no_data` (must say there's no data, never S$0), or `refuse` (must decline, run nothing).
- **`note`** where a question is tricky or has more than one fair reading.

**The answers aren't stored in the repo.** The expected answer is whatever the reference SQL returns from gold when the eval runs. That keeps real numbers out of the public repo, and keeps answers right as the data grows ("total so far" changes every week). This is how text-to-SQL evals usually work: **execution accuracy**, comparing results, not SQL text.

**A private copy for checking:** `evals/golden-answers.private.md` holds every question with its current answer. It matches `*.private.*` in `.gitignore`, so git never sees it. Verified with `git check-ignore -v evals/golden-answers.private.md` → `.gitignore:14:*.private.*`.

Mistakes caught while building this step (both worth remembering):

- **A `.gitignore` rule with a comment on the same line doesn't work:** `*.private.*  # note` makes the comment part of the pattern. The first `git check-ignore` printed nothing, so the private file would have been committed. Comments go on their own line.
- **YAML values starting with a quote:** `note: "This term" = ...` ends the value at the second quote and then fails. Long notes now use `note: >-` (a folded block).
**Checked, independently (30 Oct):** every answer was recomputed by a second route: SQL written separately on `silver.transactions`, without gold's `living_amount`, `monthly_summary` or `dim_date` (tracked days rebuilt from each tab's first and last date). **53 of 53 answerable questions match**; the 2 `refuse` questions have no answer to compare. The checker itself was tested by feeding it a deliberately wrong query, which it reported as a difference. The agent's model (Qwen) was *not* used: the answer key must not come from the thing being graded.

- After reading the answers, q35 was reworded ("no everyday purchases", since a day with only work purchases counted as zero) and q37's week start was cast to a date.

Test (PowerShell, in the project folder):

```powershell
uv run python -c "import yaml; g = yaml.safe_load(open('evals/golden.yml', encoding='utf-8')); print(len(g['questions']), 'questions')"
git check-ignore -v evals/golden-answers.private.md
git status --short -uall evals
```

Expect `55 questions`; the ignore rule on `.gitignore:14`; and only `evals/golden.yml` listed as new. Then open `evals/golden-answers.private.md` and check every answer against what you know of your data.

Commit, short version:

```
git add evals/golden.yml .gitignore docs
git commit -m "feat(evals): add golden question set with reference sql"
```

Commit with a body:

```
git add evals/golden.yml .gitignore docs
git commit -m "feat(evals): add golden question set with reference sql" -m "- 55 questions in 7 groups, levels easy to trick, types value/no_data/refuse
- expected answers come from reference sql at eval time, never stored (public repo)
- ignore *.private.* for the hand-check copy of the answers
- docs: phase 3 step 6"
```

## Step 7: eval runner (`agent/evals.py`, `uv run eval`)

For each golden question: run the reference SQL (the expected answer), ask the agent, score the answer text.

**Three different queries, easy to mix up:**

| | Who writes it | Where it lives | Its job |
| --- | --- | --- | --- |
| Reference query | Claude, by hand | `evals/golden.yml` → `sql:` | The answer key: running it gives the correct answer |
| Cross-check query | Claude, separately, on silver | Not in the repo (step 6 only) | Checks the answer key itself |
| The agent's query | Qwen, live, during `uv run eval` | `evals/runs/*.private.json` → `agent_sql` | The thing being tested |

The agent only ever gets the question text, never the reference query. A FAIL means the agent's *answer sentence* didn't contain the expected values. Its SQL can be right and the answer still wrong (q18: right SQL, then it subtracted July again in its head).

```
golden.yml: question + reference sql
   │ run reference sql            │ send only the question
   ▼                              ▼
expected rows                Qwen: own SQL → own sentence
   └──── every expected value in the sentence? ────┘ → PASS / FAIL
```

- **`value`:** every cell of the expected result must appear in the answer. Numbers are pulled out of the text (`S$8,129.57` → 8129.57) and must match to the cent; amounts of 100 or more may be rounded to whole dollars. Dates may be written several ways (`2026-09-28`, `28 September 2026`). Labels (a month, a term, a category) must appear as words.
- **`no_data`:** must say there's no data (or that I wasn't tracked / was in Thailand), and must **not** contain S$0, even next to "no data".
- **`refuse`:** must decline ("can't", "read-only", "there is no …").
- Prints `PASS`/`FAIL`, seconds and tool steps per question, then the score by level. The full report (answers, the agent's SQL, the expected rows) goes to `evals/runs/<time>.private.json`, git-ignored because it holds real numbers.
- `--only q01,q05` and `--level easy` run a subset; `--model` tries another endpoint.
- `tests/agent/test_evals.py`: 12 scoring tests (formats, rounding, labels, dates, every row present, S$0 with "no data" still fails, refusals, a failed reference query never passes).

**First full run (30 Oct, Qwen 3.5 122B): 47/55 (85%)**, about 6.5 s and 4 tool steps per question, ~6 minutes in total.

| Level | Score |
| --- | --- |
| easy | 16/19 |
| medium | 19/21 |
| hard | 4/6 |
| trick | 8/9 |

Every failure was read before trusting the score. Two looked like **my** mistakes and were fixed first:

- q24 asked "per month" without saying which months; the question now says "counting every month I was in Singapore".
- q55's refusal check only knew words like "can't"; it now also accepts "there is no …" and "not available".

Re-running those two, both still failed, now for real: on q24 the agent *said* it counted every month but divided by the months with laundry; on q55 it called a gold table "bronze-level data", a different answer from the first run. So all 8 failures are the agent's:

| Question | What went wrong |
| --- | --- |
| q18 Y1S1 without July | Right SQL, then subtracted July again in its head (the model did arithmetic) |
| q22 travel in a month | Gave the month's whole living spend, not the Travel category |
| q24 laundry per month | Said "every month", divided by months with laundry |
| q27 MRT trips | Counted every Travel row, instead of matching "mrt" in item names |
| q33 weekend vs weekday | Listed each weekday instead of the two averages |
| q39 tuition | Summed all one-offs (tuition + hall fees) |
| q40 hall fees | Counted hall money *received* as hall fees paid (no direction filter) |
| q55 bronze | Didn't decline; called a gold table "bronze-level" |

Lesson: **read the failures before trusting a score.** A failure can be the agent, the question, or the scorer; only the first kind is fixed in the agent.

Test (PowerShell, in the project folder):

```powershell
uv run pytest
uv run eval --only q01,q13,q47,q51,q54
uv run eval
```

Expect all tests to pass; the 5 sample questions to pass; and a full score near 47/55 (the model isn't perfectly repeatable, so ±1–2 is normal). `git status` must not list anything under `evals/runs/`.

Commit, short version:

```
git add src/analyst_agent/agent/evals.py tests/agent/test_evals.py evals/golden.yml pyproject.toml uv.lock CLAUDE.md docs
git commit -m "feat(evals): add eval runner and scoring"
```

Commit with a body:

```
git add src/analyst_agent/agent/evals.py tests/agent/test_evals.py evals/golden.yml pyproject.toml uv.lock CLAUDE.md docs
git commit -m "feat(evals): add eval runner and scoring" -m "- uv run eval: reference sql vs agent answer for every golden question
- scoring for value, no_data and refuse questions, with 12 tests
- full reports saved as evals/runs/*.private.json (git-ignored)
- golden q24 made unambiguous
- baseline: 47/55 on qwen 3.5 122b"
```

## Step 8a: fix the agent's behaviour (the prompt)

The 8 failures from step 7 had 5 root causes. Behaviour problems go in the prompt (`SYSTEM_PROMPT` in `agent/loop.py`); meaning problems go in the semantic layer (8b).

**Rule for every fix: general, never about one golden question.** Writing "for tuition use `LIKE '%tuition%'`" would pass q39 by teaching the answer key, and the score would stop meaning anything (**overfitting to the test set**). One draft rule even used "weekends or weekdays" as its example, which is golden q33 word for word; it was swapped for an example that isn't in the test.

Prompt changes:

- **Rule 2, numbers only from SQL,** made concrete: never add, subtract, divide or adjust a returned number, "not even to remove something"; a derived number needs its own SQL. (The old "never do arithmetic in your head" was ignored in q18.)
- **Rule 4, answer exactly as asked:** the same grouping and comparison as the question.
- **Rule 5, describe the method truthfully:** never claim a method the SQL didn't use (q24 said "every month" and divided by months with laundry).
- **Rule 6, decline instead of substituting:** other tables, raw data, changes or deletes → say you can't and stop; don't offer other data.

**The scorer had bugs too,** found by reading every result instead of trusting the score:

- **False pass, too lenient:** q39 passed with a wrong headline ("S$23,729.33 in tuition and hall fees") because the expected number appeared in a breakdown further down. Now the expected values must be in the **lead** (the first paragraph), unless the lead has no amount at all (e.g. "Your top 3 categories are:" before a list). A first version counted "top **3**" as a number; a test caught it, so the lead must hold an S$ value, a decimal or a percentage.
- **False pass, too lenient (from step 7):** widening the refusal words to "there is no …" passed q55's baseline answer, which said "there is no bronze table" and then showed 200 rows anyway. Now a `refuse` question passes only if the agent declines **and runs no query**.
- **False fail, too strict:** q20's answer "68.66%" failed against "68.7". A more precise answer is now rounded to the expected value's decimals before comparing; a less precise one ("69%") still fails.
- **`--rescore REPORT`:** re-scores a saved report with the current scorer, with no agent calls. Needed because comparing two scores only means something if both used the same scorer.

Results (both runs scored with the final scorer):

| Run | Score | Failures |
| --- | --- | --- |
| Baseline (step 7) | 47/55 (85%) | q18, q22, q24, q27, q33, q39, q40, q55 |
| After 8a | **50/55 (91%)** | q22, q24, q27, q39, q40 |

Fixed by the prompt: q18 (arithmetic), q33 (answer as asked), q55 (decline). q24 no longer loops, but still divides by the wrong months. A side effect seen on the way: with the stricter "never divide yourself" rule, q24 once ran the same query until it hit the 8-step limit; a rule about *what* to compute (8b) is the real fix. No question that passed before fails now.

Test (PowerShell, in the project folder):

```powershell
uv run pytest
uv run eval --rescore evals/runs/<baseline report>.private.json
uv run eval
```

Expect all tests to pass; the baseline re-scores to 47/55; a full run near 50/55 (±1–2, the model isn't perfectly repeatable).

Commit, short version:

```
git add src/analyst_agent/agent/loop.py src/analyst_agent/agent/evals.py tests/agent/test_evals.py docs
git commit -m "feat(agent): tighten prompt rules and eval scoring"
```

Commit with a body:

```
git add src/analyst_agent/agent/loop.py src/analyst_agent/agent/evals.py tests/agent/test_evals.py docs
git commit -m "feat(agent): tighten prompt rules and eval scoring" -m "- prompt: numbers only from sql, answer as asked, truthful method, decline instead of substituting
- scorer: values must be in the answer's lead; refuse means no query; precise answers round to expected decimals
- uv run eval --rescore to re-score saved reports with the current scorer
- eval: 47/55 -> 50/55 on the same scorer"
```

## Step 8b: fix the agent's knowledge (the semantic layer)

Meaning problems go in `semantic/money.yml`. One rule per root cause, each tested on the questions it targets plus their neighbours (to catch regressions), then a full run at the end.

### 8b-0: the examples were leaking the test

The semantic layer's 4 examples were written before the golden set, and some were near-copies of golden questions ("How much did I spend in March 2026?" = q01). That's **data leakage**: the agent saw test questions, with their SQL, before the test. Worse, the tuition example filtered on `is_one_off` alone, which includes hall fees: it *taught* q39's mistake. One example also used a real item name (against `CLAUDE.md`).

They were replaced with different questions that show the same patterns (a monthly lookup, a per-day comparison by study year, matching an item, a category in a period joined to `dim_date`). A comment in the file now says examples must differ from the golden questions.

Honest result: **48/55**. Reading the failures again found two more things:

- **Scorer false fail:** "no data (not even S$0)" failed the `no_data` check because it contains "S$0". Now "not S$0" / "not even S$0" is ignored. Re-scored: **50/55**.
- **Agent bug:** q36 ran the right query and then replied with nothing. The loop now asks once more ("You haven't written an answer yet…") when the final reply is empty. Tested with a scripted fake model.

### 8b-1 to 8b-5: one rule per root cause

| Rule | Root cause | Questions it fixed |
| --- | --- | --- |
| **Category or item?** A named category filters `category`; anything more specific matches `item_clean LIKE`; `monthly_summary` has no categories | Used the whole Travel category for one kind of trip; used the month's total for one category | q27, q22 |
| **`is_one_off` mixes two kinds and both directions**, so never sum it alone; tuition vs hall by item, paid vs received by `txn_kind` | Summed all one-offs; counted hall money received as hall fees paid | q39, q40 |
| **Money direction:** paid/spent/cost = out; received/got/paid back = in | Same as above | q40 |
| **Per-month averages** divide by months with data, in SQL; the `COUNT(DISTINCT month)` trap named | Divided by months with the item only | q24 (flaky, see below) |
| **A month without a year** = the most recent one with data; say which year | Guessed a year with no data | q52 |
| **`item_clean` is lower case**, so write LIKE patterns in lower case; check spelling before saying "none" | A capitalised LIKE pattern matched nothing | q27 (second cause) |

What worked, again: **concrete examples beat abstract rules.** "A transport line or app" didn't change q27; "bus rides or taxis are items inside Travel" did. Examples were chosen *not* to be the golden question (bus and taxi, not the item asked about; chicken rice, not a real item).

Is "tell tuition from hall fees with item_clean" overfitting? No: it's a fact about the data any analyst needs, contains no answer, and helps any tuition or hall question, including new ones.

**YAML colons, a third time:** "the category: one category holds…" and "Trap to avoid: …" broke the file again; one was caught only because the test run printed "1 error" (the filter showed "passed|failed" only). All rules are now folded blocks (`- >-`), where a colon is just text. Lesson: run the tests after every change, and make the test output filter show errors.

### Results

| Run | Score | Failures |
| --- | --- | --- |
| Baseline (step 7) | 47/55 | q18, q22, q24, q27, q33, q39, q40, q55 |
| After 8a (prompt) | 50/55 | q22, q24, q27, q39, q40 |
| 8b-0, leak-free examples (re-scored) | 50/55 | q24, q27, q36, q39, q40 |
| After the 8b rules, full run 1 | **53/55** | q24, q52 |
| After the 8b rules, full run 2 | **53/55** | q24, q27 |

The last misses are **flaky**: q52 and q27 then passed 3 of 3 targeted runs after their rules (no-year, lower-case). **q24 is a known weakness:** with the per-month rule it passes some runs and fails others, sometimes contradicting itself ("every month… excluding months with no laundry"). **Stopped tuning it on purpose:** rewording rules until one question passes is overfitting too. Better fixes come later: retrieved few-shot examples (Phase 4) or a stronger model.

Test (PowerShell, in the project folder):

```powershell
uv run pytest
uv run eval --only q22,q27,q39,q40,q52
uv run eval
```

Expect all tests to pass; the 5 targeted questions to pass; a full run around 53/55 (q24 may fail; one other question may flake).

Commit (all of step 8), short version:

```
git add semantic/money.yml src/analyst_agent/agent tests/agent docs
git commit -m "feat(agent): fix agent from eval failures, 47 to 53 of 55"
```

Commit with a body:

```
git add semantic/money.yml src/analyst_agent/agent tests/agent docs
git commit -m "feat(agent): fix agent from eval failures, 47 to 53 of 55" -m "- prompt: numbers only from sql, answer as asked, truthful method, decline instead of substituting
- semantic layer: leak-free examples; rules for category vs item, one-offs and direction, per-month, month without year, lower-case matching
- loop: ask again after an empty reply
- scorer: answer in the lead, refuse means no query, precise answers round, no-data answers may say not S$0; --rescore
- eval: 47/55 -> 53/55 (same scorer); q24 still flaky"
```

## Step 9a: can we run Genie from code?

**Genie** is Databricks' built-in "ask your data" assistant. It's the baseline: the free option the agent has to beat, or the custom work wasn't worth it.

Read-only checks, nothing changed:

- The Genie API responds on Free Edition, and the workspace already has a sample space.
- The Python SDK has `create_space` and `start_conversation_and_wait`, so both setup and questions can be scripted.
- A space is defined by a JSON document (`serialized_space`), with slots for tables, text instructions and example SQL. Those slots match our semantic rules and examples.
- Speed: about 5 questions a minute per workspace, according to Databricks community posts. **Not verified** in the official docs or for Free Edition.

### The rule, fixed before seeing any scores

Two rounds on one space:

- **Round A, out of the box:** the tables only.
- **Round B, with our knowledge:** the same space plus our semantic rules and the 4 leak-free examples.

**The gate is round B**, the fair comparison. Choosing the rule after seeing the scores would let the result pick the rule.

## Step 9b: create the space, ask one question

`src/analyst_agent/agent/genie.py`, with the command `uv run genie`:

- `space_definition`: the space's contents. Tables only for now.
- `setup_space`: creates the space, or finds it by title if it exists. The id is never written down, so it can't end up in this public repo.
- `answer_from`: Genie's reply is a list of attachments (text or a query). This turns them into text, SQL and tables. Tested with a fake message.
- `ask_genie`: one question in a new conversation, so each question starts fresh, like the agent.

What the first run showed:

- **The space was created,** but the lookup straight afterwards didn't find it. A few seconds later it was listed, once. The listing lags slightly behind a create (**eventual consistency**). Don't run `setup` twice in quick succession, or it could make a second space.
- **The test question worked** and matched a separate reference query. The question isn't in the golden set. One question says nothing about Genie's quality; it only proves the plumbing.
- **Genie's reply has a different shape from the agent's:** first a line restating the question (the query's description), then the answer in text, plus its query and result table. The scorer has to handle this fairly, which is a decision for step 9c.

Test (PowerShell, in the project folder):

```powershell
uv run pytest tests/agent
uv run genie setup
uv run genie ask "How many everyday purchases did I make in November 2025?"
```

Expect the tests to pass; "already exists"; then a status of COMPLETED with text, a query and a table.

Commit, short version:

```
git add pyproject.toml src/analyst_agent/agent/genie.py tests/agent/test_genie.py docs
git commit -m "feat(genie): create baseline genie space and ask it from code"
```

Commit with a body:

```
git add pyproject.toml src/analyst_agent/agent/genie.py tests/agent/test_genie.py docs
git commit -m "feat(genie): create baseline genie space and ask it from code" -m "- uv run genie setup: create the space on the gold tables, found by title so no id is in the repo
- uv run genie ask: one question in a new conversation, with text, sql and tables
- fix the gate before scoring: the agent must beat genie with our rules and examples"
```

## Step 9c: score Genie, tables only (round A)

`uv run eval --genie` asks the Genie space the same 55 questions and scores it with the same `score()`. It's paced to one question every 13 seconds to stay under the rate limit.

What counts as Genie's answer, decided before the run:

- **Scored:** only its written answer, as with the agent. Not the line restating the question.
- **Recorded, not scored:** whether its result tables alone would pass. That separates wrong queries from wrong writing. It's a hint: labels and dates are formatted differently in a table, and a long table can pass by chance.

### Testing the test, twice more

- **False fail:** on a 3-question trial, Genie correctly declined to delete data and ran no query, but failed because its wording wasn't in the refusal list. The list was extended. A plain `$0` now also counts as `S$0`, because Genie doesn't know the currency.
- **False pass:** q08 asks about "this month", and the data had no rows for the current month. With nothing expected, the scorer said "all values present". Genie had given last month's total as this month's. Now a reference query with no rows means the right answer is "no data". Found only by reading the passes too, not just the failures. Re-scored: 32 → **31**.

The agent's last full run, re-scored with the final scorer, stays at 53. `--rescore` prints the new score but doesn't change the saved report.

### Results

| Level | Agent (31 Oct) | Genie round A (5 Nov) |
| --- | --- | --- |
| Easy | 19/19 | 13/19 |
| Medium | 20/21 | 11/21 |
| Hard | 5/6 | 3/6 |
| Trick | 9/9 | 4/9 |
| **Total** | **53/55** | **31/55** |

All 24 Genie failures were read by hand. None was a scorer error.

| Why Genie failed | Questions | Count |
| --- | --- | --- |
| Doesn't know what "spending" means here, so it adds tuition, money coming in or work money | q18, q19, q20, q21, q23, q26, q28, q31, q39, q40, q46, q53 | 12 |
| Reports 0 for a period with no data, or last month's total as this month's | q08, q47, q48, q49, q50 | 5 |
| Doesn't know how this data is organised (category vs item, how a kind of row is labelled or spelled) | q25, q42, q43, q45 | 4 |
| Query logic (compares with only one term, a join that multiplies rows, counts back from today instead of the last data) | q13, q24, q38 | 3 |

**Every wrong answer came from a wrong query:** no question failed in writing while its table would have passed. Genie is good at direct lookups, and it declined both refuse questions.

### How to read this honestly

- **Not yet a fair fight.** The agent was tuned on these exact questions in step 8, while Genie got no help. That's why round A isn't the gate. Most of its failures are missing knowledge, which round B supplies.
- **Different days.** The agent's run is from 31 Oct, and the data has changed since. Each run compares against the reference answers of its own day, so each score is valid, but step 9d runs both on the same day.
- **q08 changes with the date.** On 31 Oct it had an answer; this month it's "no data", because the data currently ends on 31 Oct. The nightly ingest is succeeding, so November just isn't in the sheet yet.

Test (PowerShell, in the project folder; about 15–20 minutes):

```powershell
uv run pytest tests/agent
uv run eval --genie
```

Expect the tests to pass, and a total near 31/55. Genie's answers can vary between runs.

## Step 10: a simple chat page

`app/chat.py` is a Streamlit page. It's thin, like the notebooks: the agent is still `ask` in `src`. It shows the question, the answer, the SQL in a fold-out section, and the steps and time taken.

Streamlit reruns the whole script on every interaction, so normal variables are wiped each time. The chat history lives in `st.session_state`, which survives reruns.

What would have gone wrong:

- Streamlit reads `$...$` as a math formula, which garbles two S$ amounts in one answer. Dollar signs are escaped, and a test covers it.
- By default the page can be reached from other devices on the network and sends usage stats, and it has a Deploy button. `.streamlit/config.toml` limits it to localhost, turns the stats off and hides the button. It's only read when the page is started **from the project folder**.

`streamlit` is a dev dependency, so it isn't part of the package.

Limits: each question starts fresh, so the page shows history but the agent doesn't see it; and it's laptop only, because it shows real data. A public version waits for step 11.

Checked: 3 tests run the page with no browser and a fake agent; one real question in Chrome (amounts displayed correctly, SQL section appeared, Deploy button gone); `netstat` shows it listening on `127.0.0.1` only. Stopping the usage stats is set in the config but wasn't separately checked.

Test (PowerShell, in the project folder):

```powershell
uv run streamlit run app/chat.py
```

Expect a page at `http://localhost:8501`. Ask a question; Ctrl+C in the terminal stops it.

Commit, short version:

```
git add pyproject.toml uv.lock CLAUDE.md src/analyst_agent/agent tests/agent docs
git commit -m "feat(evals): score genie round a, 31 of 55"
```

Commit with a body:

```
git add pyproject.toml uv.lock CLAUDE.md src/analyst_agent/agent tests/agent docs
git commit -m "feat(evals): score genie round a, 31 of 55" -m "- uv run eval --genie, paced; score the written answer, record the tables as a hint
- scorer: plain dollar zero counts, more refusal wordings, a reference with no rows means no data
- add streamlit as a dev dependency for the chat page
- agent 53/55, genie with tables only 31/55; the gate is round b"
```

## Open items

- **Read-only identity:** `run_sql` uses my own login; the guardrail is the SQL parser only.
- **Genie on Free Edition:** not yet tried.
- **Answer stability over many runs:** two full runs after step 8 both scored 53/55 but missed different questions; q24 flips between pass and fail. A `--repeat N` option (run each question N times, report the pass rate) would measure this properly.
- **q24 (per-month average)** is a known weakness of the current model; parked until Phase 4 (retrieved examples) or a model comparison.
- **Questions whose reference answer changes over time** (q07, q08, q10, q13, q38 and other totals) are fine because answers are computed at eval time, but a hand-checked number will drift after the next sync.
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
- **Reference query:** the trusted SQL for a golden question; its result is the expected answer.
- **Independent check:** computing the same answer by a different route (here silver instead of gold, separately written SQL); agreement means both are probably right, disagreement means one has a bug.
- **Testing the test:** feeding a check a case it must fail, to prove it can fail.
- **`.gitignore` pattern:** `*.private.*` matches any file with `.private.` in its name, anywhere in the repo.
- **Scorer:** the code that decides pass or fail. It can be too strict (fails a fair answer) or too lenient (passes a wrong one), so its rules are tested too.
- **Baseline score:** the first score, that later changes are compared with.
- **Non-determinism:** the same input giving different outputs; LLMs do this a little even at temperature 0.
- **Eval-driven development:** change → re-run the eval → keep the change only if the score goes up (and nothing else breaks).
- **Overfitting to the test set:** tuning the agent to the exact golden questions, so the score rises without the agent getting better at new questions.
- **Regression:** something that worked before and broke after a change; why every fix is checked on all 55 questions, not only the ones it targets.
- **False pass / false fail:** the scorer passing a wrong answer / failing a right one. Both make the score lie.
- **Re-scoring:** applying a changed scorer to saved answers, so old and new runs stay comparable.
- **Data leakage:** test material (questions or their answers) showing up in what the system learns from, here the semantic layer's examples. It inflates the score.
- **Flaky test:** one that passes or fails from run to run with no change. Judge it over several runs, not one.
- **Stop rule:** deciding in advance when to stop tuning for one failure, so the fix doesn't turn into overfitting.
- **Vacuous pass:** a check that passes because there was nothing to check, like q08's empty reference.
- **Streamlit rerun model:** the whole script runs again on every interaction; `st.session_state` is what survives.
- **Baseline:** the simple or existing option you compare against. A score means little until you know what the easy alternative gets.
- **Eventual consistency:** a change is saved, but a read right afterwards may not show it yet. Here, a new Genie space took a few seconds to appear in the list.
