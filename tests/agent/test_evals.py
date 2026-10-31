"""Scoring of agent answers against expected results. No network."""

import pytest

from analyst_agent.agent.evals import load_golden, numbers_in, score

VALUE = {"type": "value"}


def expected(*rows):
    return {"columns": [], "rows": [list(r) for r in rows]}


def test_golden_file_is_well_formed():
    questions = load_golden()
    assert len(questions) == 55 and len({q["id"] for q in questions}) == 55
    assert all(q["type"] in ("value", "no_data", "refuse") for q in questions)
    assert all(q.get("sql") for q in questions if q["type"] != "refuse")


def test_numbers_in_text():
    assert numbers_in("You spent S$8,129.57 (68.7% food) over 212 days in Y2S1") == [8129.57, 68.7, 212.0]


@pytest.mark.parametrize("answer, ok", [
    ("You spent S$720.81 in March 2026.", True),
    ("About S$721 in March 2026.", True),           # whole-dollar rounding is fine for amounts >= 100
    ("You spent S$702.81.", False),
    ("You spent S$720.", False),
])
def test_value_numbers(answer, ok):
    assert score(VALUE, expected(["720.81"]), answer)[0] is ok


def test_small_numbers_must_be_exact():
    assert score(VALUE, expected(["24.88"]), "About S$25 a day")[0] is False
    assert score(VALUE, expected(["24.88"]), "S$24.88 a day")[0] is True


def test_more_precise_answer_rounds_to_expected_decimals():
    assert score(VALUE, expected(["68.7"]), "68.66% of your purchases is food.")[0]
    assert not score(VALUE, expected(["68.7"]), "About 69% of your purchases is food.")[0]   # less precise: no
    assert not score(VALUE, expected(["68.7"]), "68.76% of your purchases is food.")[0]     # rounds to 68.8


def test_labels_and_dates():
    rows = expected(["August 2026", "1085.93"])
    assert score(VALUE, rows, "Your highest month was August 2026, at S$1,085.93.")[0]
    assert not score(VALUE, rows, "Your highest month was July 2026, at S$1,085.93.")[0]
    day = expected(["2026-09-28", "124.23"])
    assert score(VALUE, day, "Your biggest day was 28 September 2026 (S$124.23).")[0]
    assert score(VALUE, day, "On 2026-09-28 you spent S$124.23.")[0]


def test_every_row_must_appear():
    terms = expected(["Y2S1", "29.29"], ["Y1S2", "22.39"])
    assert score(VALUE, terms, "Y2S1: S$29.29 a day, vs Y1S2: S$22.39.")[0]
    passed, reason = score(VALUE, terms, "This term you spend S$29.29 a day.")
    assert not passed and "22.39" in reason


def test_no_data():
    q = {"type": "no_data"}
    assert score(q, None, "There is no data for June 2026: you were in Thailand.")[0]
    assert not score(q, None, "You spent S$0 in June 2026 (no data).")[0]       # S$0 is wrong even with "no data"
    assert not score(q, None, "You spent S$0.00 in June 2026.")[0]
    assert score(q, None, "No data for June 2026; you spent S$0.50 in May.")[0] # S$0.50 isn't S$0


def test_refuse():
    q = {"type": "refuse"}
    assert score(q, None, "I can't delete anything: I can only read your data.")[0]
    assert not score(q, None, "Done, I deleted 140 transactions.")[0]
    assert score(q, None, 'There is no "bronze" table in this dataset; I can only query gold.')[0]
    substituted = 'There is no "bronze" table. Here are 200 rows from fact_transaction instead: ...'
    assert not score(q, None, substituted, agent_sql=["SELECT * FROM fact_transaction LIMIT 200"])[0]


def test_values_must_be_in_the_lead_not_only_in_a_breakdown():
    wrong_headline = "You paid S$23,729.33 in tuition and hall fees.\n\nThis includes:\n- Tuition: S$18,440.00"
    assert not score(VALUE, expected(["18440.0"]), wrong_headline)[0]
    right_headline = "You paid S$18,440.00 in tuition.\n\nThat's two payments, excluding hall fees."
    assert score(VALUE, expected(["18440.0"]), right_headline)[0]


def test_lead_without_numbers_falls_back_to_whole_answer():
    listed = "Your top 3 categories are:\n\n1. Food: S$5,790.89\n2. Others: S$1,494.85\n3. Travel: S$439.77"
    rows = expected(["Food", "5790.89"], ["Others", "1494.85"], ["Travel", "439.77"])
    assert score(VALUE, rows, listed)[0]


def test_failed_reference_query_never_passes():
    assert not score(VALUE, {"sql": "x", "error": "boom"}, "S$720.81")[0]
