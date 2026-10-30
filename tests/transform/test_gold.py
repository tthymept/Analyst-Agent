"""Gold tables on a small made-up silver table."""

import datetime as dt

import pytest

from analyst_agent.transform.gold import daily_living_spend, dim_date, fact_transaction, gold_tests, monthly_summary

SILVER = ("source_tab string, source_row int, txn_date date, item_clean string, amount_sgd double, "
          "direction string, txn_kind string, is_one_off boolean, is_reimbursable boolean")
D = dt.date


@pytest.fixture(scope="module")
def gold(spark):
    silver = spark.createDataFrame([
        ("8/25", 2, D(2025, 7, 30), "lunch", 5.0, "out", "expense", False, False),          # arrival week
        ("8/25", 3, D(2025, 8, 1), "start", 1000.0, "in", "opening_balance", False, False),
        ("8/25", 4, D(2025, 8, 1), "dinner", 8.0, "out", "expense", False, False),
        ("8/25", 11, D(2025, 8, 1), "dinner payback", 3.0, "in", "refund", False, False),  # friend's share
        ("8/25", 5, D(2025, 8, 3), "hall fee", 1400.0, "out", "expense", True, False),      # one-off
        ("8/25", 6, D(2025, 8, 3), "club snacks", 40.0, "out", "expense", False, True),     # work money
        ("8/25", 7, D(2025, 8, 4), "club payback", 40.0, "in", "refund", False, True),
        ("8/25", 8, D(2025, 8, 4), "correction", 2.0, "out", "adjustment", False, False),
        ("8/25", 9, D(2025, 8, 4), "mom", 500.0, "in", "funding", False, False),
        ("8/25", 10, D(2025, 8, 5), "interest earned", 0.05, "in", "income", False, False),  # last tracked day
        ("1/26", 2, D(2026, 1, 6), "morning set 1", 2.5, "out", "expense", False, False),
    ], SILVER)
    fact = fact_transaction(silver)
    dates = dim_date(silver)
    daily = daily_living_spend(fact, dates)
    monthly = monthly_summary(fact, dates)
    return fact, dates, daily, monthly


def test_living_amount(gold):
    fact = {r.source_row: r.living_amount for r in gold[0].filter("source_tab = '8/25'").collect()}
    assert {row: amount for row, amount in fact.items() if amount is not None} == {2: 5.0, 4: 8.0, 11: -3.0}


def test_dates_tracked_only_while_logging(gold):
    dates = {r.date: r for r in gold[1].collect()}
    assert dates[D(2025, 7, 30)].is_tracked and dates[D(2025, 8, 5)].is_tracked
    assert not dates[D(2025, 7, 29)].is_tracked and not dates[D(2025, 8, 6)].is_tracked
    assert not dates[D(2025, 12, 15)].is_tracked                         # December in Thailand
    assert (dates[D(2025, 7, 30)].term, dates[D(2026, 1, 6)].term) == ("Y1S1", "Y1S2")
    assert min(dates) == D(2025, 7, 1) and max(dates) == D(2029, 5, 31)


def test_daily_includes_zero_days(gold):
    daily = {r.date: r.living_spend for r in gold[2].collect()}
    assert daily[D(2025, 8, 1)] == 5.0                                   # 8.00 dinner - 3.00 paid back
    assert daily[D(2025, 8, 2)] == 0.0                                   # tracked, nothing bought
    assert D(2025, 8, 6) not in daily                                    # not tracked: no row at all


def test_monthly_summary(gold):
    aug = gold[3].filter("month = DATE'2025-08-01'").first()
    assert (aug.tracked_days, aug.living_spend, aug.avg_daily_living_spend) == (5, 5.0, 1.0)
    assert (aug.living_purchases_total, aug.paybacks_received) == (8.0, 3.0)   # reimbursable payback not counted
    assert (aug.one_off_spend, aug.reimbursable_paid, aug.reimbursable_received) == (1400.0, 40.0, 40.0)
    assert (aug.adjustments, aug.funding_received, aug.income_received) == (2.0, 500.0, 0.05)
    jul = gold[3].filter("month = DATE'2025-07-01'").first()
    assert (jul.tracked_days, jul.living_spend, jul.term) == (2, 5.0, "Y1S1")   # 30-31 Jul
    assert gold[3].count() == 3                                          # Jul, Aug, Jan only


def test_gold_tests_pass(gold):
    fact, _, daily, monthly = gold
    assert all(r["failures"] == 0 for r in gold_tests(fact, daily, monthly).values())
