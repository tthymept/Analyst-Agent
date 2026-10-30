"""Silver cleaning on a small fake workbook that has the same quirks as the real one.

The real data never goes in the repo; these rows are made up.
"""

import datetime as dt
import json

import pytest

from analyst_agent.transform import rules
from analyst_agent.transform.silver_transactions import (balance_check, build_silver, data_tests,
                                                          flag_reimbursable)

SNAPSHOT = "snapshot_20261019T000000Z.json"


def serial(y, m, d):
    """Google Sheets date serial: days since 1899-12-30."""
    return (dt.date(y, m, d) - dt.date(1899, 12, 30)).days


def tab_rows(tab, index, header, rows, formulas=None):
    """Bronze rows for one tab, cells JSON-encoded like the ingest writes them."""
    formulas = formulas or {}
    out = [(SNAPSHOT, "2026-10-19T00:00:00+00:00", tab, index, 1,
            [json.dumps(h) for h in header], [json.dumps(h) for h in header], [json.dumps(h) for h in header])]
    for i, row in enumerate(rows, start=2):
        raw = [json.dumps(c) for c in row]
        fx = [json.dumps(formulas.get((i, j), c)) for j, c in enumerate(row)]
        out.append((SNAPSHOT, "2026-10-19T00:00:00+00:00", tab, index, i, raw, [json.dumps(str(c)) for c in row], fx))
    return out


WITH_PAYMENT = ["Date", "List", "Payment Method", "Type", "Income(SGD)", "Expense(SGD)", "Expense(THB)",
                "Remaining (SGD)", "Remianing (THB)", "Note"]
NO_PAYMENT = ["Date", "List", "Type", "Income(SGD)", "Expense(SGD)", "Expense(THB)", "Remaining (SGD)"]
FIRST_TAB = ["Date", "List", "Payment Method", "Type", "Income", "Expense(SGD)", "Expense(THB) (x25.17)"]
LATEST = ["Date", "List", "Type", "Income(SGD)", "Expense(SGD)", "Expense(THB)", "Remaining (SGD)",
          "Remianing (THB)", "Note"]


@pytest.fixture(scope="module")
def silver(spark):
    rows = []
    rows += tab_rows("1/26", 0, WITH_PAYMENT, [
        [serial(2026, 6, 1), "Remaining", "Bank", "*Income*", 500, 0, "", 500],  # 6 Jan read as 1 Jun
        ["", "Lunch", "Bank", "Food", "", 5.5],
        ["13/1", "Morning set 1", "Bank", "Travel", "", 2.5],                    # text date, wrong category
        ["", "Tuition Fee", "Bank", "Others", "", 9000],
        ["", "Split dinner", "Bank", "Food", 10, 30],                            # paid 30, got 10 back
        ["", "Correction", "", "", "", 2],
        ["", "", "", "", "Total", 9037.5],                                       # summary row
    ], formulas={(8, 5): "=SUM(F2:F7)-F5"})
    rows += tab_rows("2/26", 1, NO_PAYMENT, [
        [serial(2026, 1, 2), "Start", "*Income*", "", "", 0, 1200],              # 1 Feb read as 2 Jan
        ["", "Morning Set 1 ", "Food", "", 2.5],
        [serial(2026, 2, 3), "morning set 1", "Food", "", 2.5],
        ["", "Air-Con", "Air-Con", "", 10],
        ["", "Interest earned", "*Income*", 0.05, ""],
        ["", "Cash back", "*Income*", 0.79, ""],
        ["", "", "Food", "", 4],                                                 # purchase with no name typed
    ])
    rows += tab_rows("8/25", 2, FIRST_TAB, [
        [serial(2025, 7, 24), "Morning set 1", "Cash", "Food", "", 2.5],         # tab starts in July
        [serial(2025, 8, 1), "air con", "Cash", "Air-Con", "", 5],
    ])
    rows += tab_rows("9/26", 3, LATEST, [
        [serial(2026, 9, 1), "Start", "*Income*", "", "", 0, 800, 0, ""],
        ["", "Club snacks", "Others", "", 40, 0, 760, 0, "Reimb"],               # paid for work
        ["", "Club payback", "*Income*", 40, "", 0, 800, 0, "reimb - club"],     # and got it back
        ["", "Lunch", "Food", "", 5, 0, 795, 0, "with friends"],
    ])
    rows += tab_rows("10/25", 4, WITH_PAYMENT, [
        [serial(2025, 10, 5), "Monthly Income", "Bank", "*Income*", 1500, 0, 0, 1500],  # top row = starting money
        ["", "Lunch", "Bank", "Food", "", 5, 0, 1495],
        ["", "Monthly income", "Bank", "*Income*", 700, "", 0, 2195],              # further down = real income
    ])
    rows += tab_rows("EMPTY", 5, WITH_PAYMENT, [["", "", "", "", "", "", "", 0]])

    schema = ("snapshot_file string, snapshot_ts string, tab string, tab_index int, row_num int, "
              "raw array<string>, formatted array<string>, formula array<string>")
    old = [("snapshot_20260101T000000Z.json", *r[1:]) for r in rows[:3]]  # an older snapshot is ignored
    return build_silver(spark.createDataFrame(rows + old, schema)).cache()


def get(silver, tab, row, direction="out"):
    found = silver.filter(f"source_tab = '{tab}' AND source_row = {row} AND direction = '{direction}'").collect()
    assert len(found) == 1, f"expected one {direction} transaction at {tab} row {row}, got {len(found)}"
    return found[0]


def test_only_month_tabs_and_real_rows(silver):
    assert {r.source_tab for r in silver.select("source_tab").distinct().collect()} == {"1/26", "2/26", "8/25", "9/26", "10/25"}
    assert silver.filter("source_row = 8 AND source_tab = '1/26'").count() == 0  # Total row dropped
    unnamed = get(silver, "2/26", 8)                                            # blank item, real purchase
    assert (unnamed.txn_kind, unnamed.amount_sgd, unnamed.is_one_off) == ("expense", 4.0, False)
    assert silver.select("snapshot_file").distinct().first()[0] == SNAPSHOT


def test_dates_are_fixed_and_filled_down(silver):
    assert get(silver, "1/26", 2, "in").txn_date == dt.date(2026, 1, 6)
    assert get(silver, "1/26", 2, "in").date_fix == "serial:swapped"
    assert get(silver, "1/26", 3).txn_date == dt.date(2026, 1, 6)       # filled down
    assert get(silver, "1/26", 4).txn_date == dt.date(2026, 1, 13)      # text "13/1"
    assert get(silver, "2/26", 2, "in").txn_date == dt.date(2026, 2, 1)
    assert get(silver, "8/25", 2).txn_date == dt.date(2025, 7, 24)
    assert get(silver, "8/25", 2).date_fix == "serial:previous_month"


def test_columns_found_by_header_name(silver):
    assert get(silver, "2/26", 3).payment_method is None                 # tab has no Payment Method column
    assert get(silver, "8/25", 3).payment_method == "Cash"
    assert get(silver, "8/25", 3).amount_sgd == 5.0                      # "Income" header, shifted columns


def test_transaction_kinds(silver):
    assert get(silver, "1/26", 2, "in").txn_kind == "opening_balance"
    assert get(silver, "1/26", 2, "in").amount_sgd == 500
    assert get(silver, "2/26", 2, "in").amount_sgd == 1200               # amount taken from Remaining
    assert get(silver, "2/26", 6, "in").txn_kind == "income"
    assert get(silver, "2/26", 7, "in").txn_kind == "refund"
    assert get(silver, "1/26", 7).txn_kind == "adjustment"
    assert get(silver, "10/25", 2, "in").txn_kind == "opening_balance"    # first row, whatever its name
    assert get(silver, "10/25", 4, "in").txn_kind == "income"             # same name, not the first row
    assert get(silver, "8/25", 2).txn_kind == "expense"                   # first row, but money out


def test_split_bill_becomes_two_transactions(silver):
    assert get(silver, "1/26", 6, "out").amount_sgd == 30
    assert get(silver, "1/26", 6, "in").amount_sgd == 10
    assert get(silver, "1/26", 6, "in").txn_kind == "refund"


def test_categories_and_items(silver):
    fixed = get(silver, "1/26", 4)
    assert (fixed.category, fixed.category_raw) == ("Food", "Travel")
    assert get(silver, "2/26", 5).item_clean == get(silver, "8/25", 3).item_clean == "air con"
    assert get(silver, "2/26", 3).item_clean == "morning set 1"


def test_one_offs(silver):
    tuition = get(silver, "1/26", 5)
    assert tuition.is_one_off and tuition.excluded_in_sheet
    assert not get(silver, "1/26", 3).is_one_off


def test_data_tests_pass(silver):
    results = data_tests(silver)
    errors = {k: v for k, v in results.items() if v["severity"] == "error" and v["failures"]}
    assert errors == {}
    assert results["category_changed_by_cleaning"]["failures"] == 1


def test_reimbursable_from_note(silver):
    assert get(silver, "9/26", 3).is_reimbursable
    assert get(silver, "9/26", 4, "in").is_reimbursable
    assert not get(silver, "9/26", 5).is_reimbursable                    # a note, but not the marker
    assert not get(silver, "2/26", 3).is_reimbursable                    # tab has no Note column


def test_reimbursable_from_row_list(spark, monkeypatch):
    monkeypatch.setattr(rules, "REIMBURSABLE_ROWS", {"10/25": [7, 25]})
    df = spark.createDataFrame([("10/25", 7, None), ("10/25", 8, None), ("11/25", 7, None)],
                               "tab string, row_num int, note string")
    flags = {(r.tab, r.row_num): r.is_reimbursable for r in flag_reimbursable(df).collect()}
    assert flags == {("10/25", 7): True, ("10/25", 8): False, ("11/25", 7): False}


def test_study_year_and_semester(silver):
    terms = lambda r: (r.study_year, r.semester, r.term)
    assert terms(get(silver, "8/25", 2)) == (1, 1, "Y1S1")               # 24 Jul, before semester 1
    assert terms(get(silver, "1/26", 3)) == (1, 2, "Y1S2")
    assert terms(get(silver, "9/26", 3)) == (2, 1, "Y2S1")


def test_sheet_balance_kept(silver):
    assert get(silver, "1/26", 2, "in").sheet_balance == 500
    assert get(silver, "9/26", 3).sheet_balance == 760


SILVER_ROWS = "source_tab string, source_row int, direction string, amount_sgd double, sheet_balance double"


def test_balance_check(spark, monkeypatch):
    monkeypatch.setattr(rules, "KNOWN_BALANCE_DRIFT", {"9/26": (5.0, "row 5 never subtracts its expense")})
    silver = spark.createDataFrame([
        ("9/26", 2, "in", 800.0, 800.0),     # Start row
        ("9/26", 3, "out", 40.0, 760.0),
        ("9/26", 4, "in", 40.0, 800.0),
        ("9/26", 5, "out", 5.0, 800.0),      # sheet formula forgot this expense
        ("9/26", 6, "out", 1.0, None),       # trailing row with no balance: not compared
        ("2/26", 2, "out", 2.5, 2866.21),    # no Start row: opening comes from the first row
        ("2/26", 3, "out", 1.0, 2865.21),
        ("2/26", 4, "in", 3.0, 2868.21),
        ("8/25", 2, "out", 5.0, None),       # no Remaining column at all
    ], SILVER_ROWS)
    tabs = {r.source_tab: r for r in balance_check(silver).collect()}

    assert (tabs["9/26"].opening, tabs["9/26"].recomputed_close, tabs["9/26"].sheet_close) == (0.0, 795.0, 800.0)
    assert (tabs["9/26"].drift, tabs["9/26"].explained) == (5.0, True)
    assert (tabs["2/26"].opening, tabs["2/26"].drift, tabs["2/26"].explained) == (2868.71, 0.0, True)
    assert tabs["8/25"].drift is None and tabs["8/25"].explained is None

    monkeypatch.setattr(rules, "KNOWN_BALANCE_DRIFT", {})
    assert balance_check(silver).filter("explained = false").count() == 1
