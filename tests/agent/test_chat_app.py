"""The chat page, run without a browser and with a fake agent. No network."""

from pathlib import Path
from unittest.mock import patch

import pytest

from analyst_agent.agent.loop import Answer

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[2] / "app" / "chat.py")


def ask_on_page(reply) -> AppTest:
    with patch("analyst_agent.agent.loop.ask", reply):
        return AppTest.from_file(APP, default_timeout=30).run().chat_input[0].set_value("How much?").run()


def test_answer_and_its_sql_appear_with_dollars_kept_literal():
    page = ask_on_page(lambda question: Answer(text="S$5 and S$6.", sql=["SELECT 1"], steps=2))
    assert not page.exception
    assert any(r"S\$5 and S\$6." == m.value for m in page.markdown)     # not read as a math formula
    assert page.code[0].value == "SELECT 1"


def test_each_question_is_kept_in_the_history():
    def reply(question):
        return Answer(text=f"Answer to {question}", steps=1)

    with patch("analyst_agent.agent.loop.ask", reply):
        page = AppTest.from_file(APP, default_timeout=30).run()
        page.chat_input[0].set_value("first").run()
        page.chat_input[0].set_value("second").run()
    shown = [m.value for m in page.markdown]
    assert shown == ["first", "Answer to first", "second", "Answer to second"]


def test_a_failed_question_shows_an_error_not_a_crash():
    def broken(question):
        raise RuntimeError("warehouse asleep")

    page = ask_on_page(broken)
    assert not page.exception
    assert "warehouse asleep" in page.error[0].value
