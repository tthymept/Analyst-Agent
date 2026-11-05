"""A simple chat page for the analyst agent. From the project folder:  uv run streamlit run app/chat.py

Only the page lives here; the agent is `ask` in src. It shows real data, so it stays on this laptop
(.streamlit/config.toml binds it to localhost). A public version waits for the synthetic dataset.
"""

import time

import streamlit as st

from analyst_agent.agent.loop import ask


def plain(text: str) -> str:
    """Streamlit reads $...$ as a math formula, which would garble "S$5 and S$6"."""
    return text.replace("$", r"\$")


def show(turn: dict) -> None:
    with st.chat_message("user"):
        st.markdown(plain(turn["question"]))
    with st.chat_message("assistant"):
        if turn.get("error"):
            st.error(f"Something went wrong: {turn['error']}")
            return
        st.markdown(plain(turn["text"]))
        if turn["sql"]:
            with st.expander(f"SQL ({len(turn['sql'])} {'query' if len(turn['sql']) == 1 else 'queries'})"):
                for sql in turn["sql"]:
                    st.code(sql, language="sql")
        st.caption(f"{turn['steps']} tool steps · {turn['seconds']:.1f} s")


st.set_page_config(page_title="Analyst agent")
st.title("Ask about my spending")
st.caption("Reads the gold tables only. Each question starts fresh: it doesn't remember the one before.")

turns = st.session_state.setdefault("turns", [])
for turn in turns:
    show(turn)

if question := st.chat_input("How much did I spend in March 2026?"):
    with st.chat_message("user"):
        st.markdown(plain(question))
    with st.chat_message("assistant"), st.spinner("Reading the semantic layer and querying the gold tables..."):
        start = time.perf_counter()
        try:
            answer = ask(question)
            turn = {"text": answer.text, "sql": answer.sql, "steps": answer.steps}
        except Exception as e:  # show the problem on the page; the history stays usable
            turn = {"error": f"{type(e).__name__}: {e}"}
    turns.append({"question": question, "seconds": time.perf_counter() - start, **turn})
    st.rerun()
