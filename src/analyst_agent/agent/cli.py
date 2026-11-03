"""Ask the analyst from the terminal:  uv run ask "How much did I spend in March 2026?" """

from __future__ import annotations

import argparse
import sys
import time

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax

from analyst_agent.agent.loop import DEFAULT_MODEL, ask, databricks_chat


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask a question about your data.")
    parser.add_argument("question", nargs="+")
    parser.add_argument("--model", help=f"Databricks serving endpoint (default {DEFAULT_MODEL}, or $ANALYST_MODEL)")
    parser.add_argument("--sql", action="store_true", help="also print the SQL that ran")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # models use characters the Windows console code page can't print

    console = Console()
    question = " ".join(args.question)
    console.print(Panel(question, title="Question", title_align="left", border_style="cyan"))

    start = time.perf_counter()
    with console.status("Reading the semantic layer and querying the gold tables..."):
        answer = ask(question, chat=databricks_chat(args.model))
    seconds = time.perf_counter() - start

    if args.sql:
        for i, sql in enumerate(answer.sql, 1):
            console.print(Panel(Syntax(sql, "sql", word_wrap=True),
                                title=f"SQL query {i}", title_align="left", border_style="magenta"))

    console.print(Panel(Markdown(answer.text), title="Answer", title_align="left", border_style="green"))
    console.print(f"[dim]{answer.steps} tool steps · {seconds:.1f} s[/dim]")


if __name__ == "__main__":
    main()