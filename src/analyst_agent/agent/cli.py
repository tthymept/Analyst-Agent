"""Ask the analyst from the terminal:  uv run ask "How much did I spend in March 2026?" """

from __future__ import annotations

import argparse
import sys

from analyst_agent.agent.loop import DEFAULT_MODEL, ask, databricks_chat


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask a question about your data.")
    parser.add_argument("question", nargs="+")
    parser.add_argument("--model", help=f"Databricks serving endpoint (default {DEFAULT_MODEL}, or $ANALYST_MODEL)")
    parser.add_argument("--sql", action="store_true", help="also print the SQL that ran")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # models use characters the Windows console code page can't print

    answer = ask(" ".join(args.question), chat=databricks_chat(args.model))
    print(answer.text)
    if args.sql:
        for i, sql in enumerate(answer.sql, 1):
            print(f"\n-- query {i}\n{sql}")
    print(f"\n({answer.steps} steps)")


if __name__ == "__main__":
    main()
