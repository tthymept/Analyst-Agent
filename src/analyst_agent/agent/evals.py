"""Score the agent on the golden question set:  uv run eval [--model ...] [--only q01,q05] [--level easy]
Score the Genie baseline with the same questions and scorer:  uv run eval --genie

For each question: run the reference SQL (the expected answer), ask the agent, then score the agent's text
against the expected result. Prints pass/fail per question and a summary; the full report, which holds real
answers, goes to evals/runs/<time>.private.json (git-ignored).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import yaml

from analyst_agent.agent import tools
from analyst_agent.agent.genie import SPACE_TITLE, ask_genie, find_space
from analyst_agent.agent.loop import DEFAULT_MODEL, ask, databricks_chat

EVALS_DIR = tools.SEMANTIC_DIR.parent / "evals"
GENIE_SECONDS_PER_QUESTION = 13  # Genie's API allows about 5 questions a minute per workspace

NO_DATA_WORDS = r"no data|not tracked|no records|no transactions|nothing (was )?(logged|recorded|tracked)|wasn't in singapore|weren't in singapore|in thailand"
ZERO_AMOUNT = r"\$\s*0(\.0+)?(?![\d.])"  # S$0 or $0: Genie doesn't know the currency is S$
REFUSE_WORDS = (r"can't|cannot|can not|unable|not able|not allowed|only (to )?(read|query|run select|analy[sz]e)|read-only|"
                r"(don't|do not) have (the ability|access|permission)|no access|permissions? to|not permitted|"
                r"not available|isn't available|there is no")


# ---------------------------------------------------------------------------- scoring (pure functions)

def numbers_in(text: str) -> list[float]:
    """Every number written in the text: 'S$8,129.57' -> 8129.57, '68.7%' -> 68.7, '212 times' -> 212."""
    return [float(n.replace(",", "")) for n in re.findall(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?", text)]


def number_found(expected: float, found: list[float], decimals: int = 2) -> bool:
    """Exact to the cent, or rounded to whole dollars for amounts of 100 or more. When the expected value
    has fewer decimals (68.7), a more precise answer (68.66) is rounded to match before comparing."""
    return any(abs(f - expected) <= 0.011 or round(f, decimals) == expected
               or (abs(expected) >= 100 and f == round(expected)) for f in found)


def date_forms(d: date) -> list[str]:
    return [d.isoformat(), f"{d.day} {d:%B %Y}", f"{d.day} {d:%b %Y}", f"{d:%B} {d.day}, {d.year}", f"{d:%B} {d.day}"]


def value_found(value: str, text: str, found: list[float]) -> bool:
    """One expected cell: a number, a date, or a label, somewhere in the answer."""
    try:
        decimals = len(value.split(".")[1]) if "." in value else 0
        return number_found(float(value), found, decimals)
    except ValueError:
        pass
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        return any(form.lower() in text for form in date_forms(d))
    except ValueError:
        pass
    return value.strip().lower() in text


def lead(answer: str) -> str:
    """The answer's first paragraph, where the prompt asks for the answer itself. If it holds no amount
    (an S$ value, a decimal or a percentage; e.g. "Your top 3 categories are:" before a list), the whole
    answer is used instead."""
    first = answer.strip().split("\n\n")[0]
    has_amount = re.search(r"s\$\s*\d|\d\.\d|\d\s*%", first.lower())
    return first if has_amount else answer


def score(question: dict, expected: dict | None, answer: str, agent_sql: list[str] = ()) -> tuple[bool, str]:
    """(passed, reason). `expected` is the reference query's run_sql result (None for refuse questions);
    `agent_sql` is every query the agent ran."""
    text = answer.lower()
    kind = question["type"]
    no_rows = expected is not None and expected.get("rows") == []  # e.g. "this month" before any is logged
    if kind == "no_data" or no_rows:
        claimed = re.sub(r"not (even )?s?" + ZERO_AMOUNT, "", text)  # "no data (not S$0)" is the right answer
        if re.search(ZERO_AMOUNT, claimed):
            return False, "reported S$0 for a period with no data"
        return (True, "said no data") if re.search(NO_DATA_WORDS, text) else (False, "didn't say there's no data")
    if kind == "refuse":  # declining means saying so AND not fetching other data instead
        if agent_sql:
            return False, "ran a query instead of declining"
        return (True, "declined") if re.search(REFUSE_WORDS, text) else (False, "didn't decline")

    if expected is None or "error" in expected:
        return False, "reference query failed"
    text = lead(text)  # the expected values must be in the answer itself, not only in a breakdown below it
    found = numbers_in(text)
    missing = [str(v) for row in expected["rows"] for v in row if v is not None and not value_found(str(v), text, found)]
    return (False, f"missing {', '.join(missing)}") if missing else (True, "all values present")


def table_would_pass(question: dict, expected: dict | None, tables: list[dict], agent_sql: list[str]) -> bool | None:
    """A side check, not the score: would the cells of the result tables alone pass? It shows whether a wrong
    answer came from a wrong query or from the writing. A long table can pass by chance, so read it as a hint."""
    if question["type"] != "value":
        return None
    cells = " ".join(str(cell) for table in tables for row in table["rows"] for cell in row)
    return score(question, expected, cells, agent_sql)[0]


# ---------------------------------------------------------------------------- runner

def load_golden() -> list[dict]:
    return yaml.safe_load((EVALS_DIR / "golden.yml").read_text(encoding="utf-8"))["questions"]


def agent_answerer(model: str | None):
    chat = databricks_chat(model)

    def answer(question: str) -> dict:
        reply = ask(question, chat=chat)
        return {"text": reply.text, "sql": reply.sql, "steps": reply.steps}

    return answer


def genie_answerer():
    space_id = find_space()
    if not space_id:
        sys.exit(f"No Genie space '{SPACE_TITLE}' yet. Run: uv run genie setup")

    def answer(question: str) -> dict:
        reply = ask_genie(space_id, question)
        return {"text": reply.text, "sql": reply.sql, "steps": len(reply.sql), "tables": reply.tables}

    return answer


def run(questions: list[dict], answerer, seconds_per_question: float = 0) -> list[dict]:
    results = []
    next_start = 0.0
    for q in questions:
        expected = tools.run_sql(q["sql"]) if q.get("sql") else None
        time.sleep(max(0, next_start - time.monotonic()))
        start = time.monotonic()
        next_start = start + seconds_per_question
        try:
            reply = answerer(q["question"])
        except Exception as e:  # one broken question shouldn't stop the run
            reply = {"text": f"ERROR: {type(e).__name__}: {e}", "sql": [], "steps": 0}
        text, sql, steps = reply["text"], reply["sql"], reply["steps"]
        seconds = round(time.monotonic() - start, 1)
        passed, reason = score(q, expected, text, sql)
        result = {"id": q["id"], "level": q["level"], "type": q["type"], "passed": passed, "reason": reason,
                  "steps": steps, "seconds": seconds, "question": q["question"], "answer": text,
                  "agent_sql": sql, "expected": expected}
        if "tables" in reply:
            result["tables"] = reply["tables"]
            result["table_passed"] = table_would_pass(q, expected, reply["tables"], sql)
        results.append(result)
        print(f"{q['id']}  {'PASS' if passed else 'FAIL'}  {q['level']:6} {seconds:5.1f}s  {steps} steps  {reason}",
              flush=True)
    return results


def summary(results: list[dict]) -> str:
    if not results:
        return "no questions matched"

    def line(label: str, rs: list[dict]) -> str:
        n = sum(r["passed"] for r in rs)
        return f"{label:8} {n:2}/{len(rs):<2} ({100 * n / len(rs):.0f}%)"

    lines = [line("total", results)]
    for level in ("easy", "medium", "hard", "trick"):
        rs = [r for r in results if r["level"] == level]
        if rs:
            lines.append(line(level, rs))
    lines.append(f"avg {sum(r['seconds'] for r in results) / len(results):.1f}s, "
                 f"{sum(r['steps'] for r in results) / len(results):.1f} steps per question")
    with_table = [r for r in results if r.get("table_passed") is not None]
    if with_table:
        lines.append(f"tables alone would pass {sum(r['table_passed'] for r in with_table)}/{len(with_table)} "
                     "value questions (a hint, not the score)")
    errors = [r["id"] for r in results if r["answer"].startswith("ERROR:")]
    if errors:  # a failed call says nothing about the answer: run these again with --only
        lines.append(f"errors, not wrong answers: {', '.join(errors)}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the agent on the golden question set.")
    parser.add_argument("--model", help=f"Databricks serving endpoint (default {DEFAULT_MODEL})")
    parser.add_argument("--genie", action="store_true", help="score the Genie space instead of the agent")
    parser.add_argument("--only", help="comma-separated question ids, e.g. q01,q47")
    parser.add_argument("--level", choices=["easy", "medium", "hard", "trick"])
    parser.add_argument("--rescore", metavar="REPORT", help="re-score a saved report with the current scorer (no agent calls)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if args.rescore:  # after changing the scorer: old answers, new rules, so scores stay comparable
        results = json.loads(Path(args.rescore).read_text(encoding="utf-8"))["results"]
        for r in results:
            passed, reason = score(r, r["expected"], r["answer"], r["agent_sql"])
            if passed != r["passed"]:
                print(f"{r['id']}  {'PASS' if r['passed'] else 'FAIL'} -> {'PASS' if passed else 'FAIL'}  {reason}")
            r["passed"], r["reason"] = passed, reason
            if "tables" in r:
                r["table_passed"] = table_would_pass(r, r["expected"], r["tables"], r["agent_sql"])
        print("\n" + summary(results))
        return

    questions = load_golden()
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    if args.level:
        questions = [q for q in questions if q["level"] == args.level]

    model = "genie" if args.genie else args.model or DEFAULT_MODEL
    print(f"{len(questions)} questions, model {model}\n")
    if args.genie:
        results = run(questions, genie_answerer(), GENIE_SECONDS_PER_QUESTION)
    else:
        results = run(questions, agent_answerer(args.model))
    print("\n" + summary(results))

    out = EVALS_DIR / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}.private.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"model": model, "results": results}, indent=2, default=str), encoding="utf-8")
    print(f"\nFull report (real answers, git-ignored): {out.relative_to(EVALS_DIR.parent)}")


if __name__ == "__main__":
    main()
