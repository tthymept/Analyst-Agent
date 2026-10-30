"""Silver transactions -> gold tables for the dashboard and the agent, in PySpark.

Gold is what everything downstream reads. Each metric is defined once, here:

    living spend = my own share of everyday spending:
                   expenses that are not one-offs (tuition, hall) and not reimbursable (work money),
                   minus paybacks (friends paying back their share: refunds that aren't work money).
                   Cashback and small gifts are income, so they don't reduce it.
                   Correction rows are their own txn_kind ('adjustment'), so they're already out.

A payback is subtracted on the day it arrives, which is often not the day of the meal it pays for, so a single
day can come out below zero. Monthly and term totals are what to read.

Months are calendar months of `txn_date`, not sheet tabs: the 8/25 tab starts on 24 Jul, and the sheet's
own August total ("Only August") leaves those days out too.
"""

from __future__ import annotations

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

from analyst_agent.transform import rules
from analyst_agent.transform.silver_transactions import study_terms


def is_living_spend() -> Column:
    return (F.col("txn_kind") == "expense") & ~F.col("is_one_off") & ~F.col("is_reimbursable")


def is_payback() -> Column:
    return (F.col("txn_kind") == "refund") & ~F.col("is_reimbursable")


def fact_transaction(silver: DataFrame) -> DataFrame:
    """Silver, plus the month and the living-spend rule, so no reader has to rebuild it.

    `living_amount` is what the row adds to living spend: + a purchase, - a payback, null otherwise.
    """
    return (
        silver.withColumn("month", F.trunc("txn_date", "month"))
        .withColumn("is_living_spend", is_living_spend())
        .withColumn("is_payback", is_payback())
        .withColumn("living_amount", F.when(F.col("is_living_spend"), F.col("amount_sgd"))
                    .when(F.col("is_payback"), -F.col("amount_sgd")))
    )


def dim_date(silver: DataFrame) -> DataFrame:
    """One row per day of the degree. `is_tracked` = I was in Singapore and logging that day.

    A day is tracked when it falls between the first and last date of a month tab. That tells "spent
    nothing" (tracked, S$0) apart from "no data" (in Thailand: June, December, most of July).
    """
    spark = silver.sparkSession
    days = spark.sql(
        f"SELECT explode(sequence(DATE'{rules.CALENDAR_START}', DATE'{rules.CALENDAR_END}', INTERVAL 1 DAY)) AS txn_date"
    )
    covered = silver.groupBy("source_tab").agg(F.min("txn_date").alias("first"), F.max("txn_date").alias("last"))
    tracked = (
        days.join(covered, (days.txn_date >= covered["first"]) & (days.txn_date <= covered["last"]))
        .select("txn_date").distinct().withColumn("is_tracked", F.lit(True))
    )
    return (
        study_terms(days)
        .join(tracked, "txn_date", "left")
        .select(
            F.col("txn_date").alias("date"),
            F.trunc("txn_date", "month").alias("month"),
            F.year("txn_date").alias("year"),
            F.date_format("txn_date", "EEEE").alias("day_of_week"),
            F.dayofweek("txn_date").isin(1, 7).alias("is_weekend"),
            "study_year",
            "semester",
            "term",
            F.coalesce("is_tracked", F.lit(False)).alias("is_tracked"),
        )
    )


def daily_living_spend(fact: DataFrame, dates: DataFrame) -> DataFrame:
    """Living spend per tracked day, including the days I spent nothing (S$0, not missing)."""
    spend = (
        fact.filter(F.col("living_amount").isNotNull())
        .groupBy(F.col("txn_date").alias("date"))
        .agg(F.round(F.sum("living_amount"), 2).alias("living_spend"),
             F.sum(F.col("is_living_spend").cast("int")).alias("purchases"))
    )
    return (
        dates.filter("is_tracked")
        .join(spend, "date", "left")
        .select("date", "month", "day_of_week", "is_weekend", "study_year", "semester", "term",
                F.coalesce("living_spend", F.lit(0.0)).alias("living_spend"),
                F.coalesce("purchases", F.lit(0)).alias("purchases"))
    )


def monthly_summary(fact: DataFrame, dates: DataFrame) -> DataFrame:
    """One row per calendar month I was in Singapore: where the money went, and where it came from."""
    def total(cond: str) -> Column:
        return F.round(F.coalesce(F.sum(F.when(F.expr(cond), F.col("amount_sgd"))), F.lit(0.0)), 2)

    money = fact.groupBy("month").agg(
        F.round(F.coalesce(F.sum("living_amount"), F.lit(0.0)), 2).alias("living_spend"),
        total("is_living_spend").alias("living_purchases_total"),
        total("is_payback").alias("paybacks_received"),
        total("txn_kind = 'expense' AND is_one_off").alias("one_off_spend"),
        total("direction = 'out' AND is_reimbursable").alias("reimbursable_paid"),
        total("direction = 'in' AND is_reimbursable").alias("reimbursable_received"),
        total("txn_kind = 'adjustment'").alias("adjustments"),
        total("txn_kind = 'funding'").alias("funding_received"),
        total("txn_kind = 'income' AND NOT is_reimbursable").alias("income_received"),
        F.sum(F.when(F.col("is_living_spend"), 1).otherwise(0)).alias("living_purchases"),
    )
    days = (
        dates.filter("is_tracked").groupBy("month")
        .agg(F.count("*").alias("tracked_days"), F.first("study_year").alias("study_year"),
             F.first("semester").alias("semester"), F.first("term").alias("term"))
    )
    return (
        days.join(money, "month", "left")
        .withColumn("avg_daily_living_spend", F.round(F.col("living_spend") / F.col("tracked_days"), 2))
        .orderBy("month")
    )


def gold_tests(fact: DataFrame, daily: DataFrame, monthly: DataFrame) -> dict[str, dict]:
    """Same shape as silver's data_tests. The three gold tables must agree on total living spend."""
    def living(df: DataFrame, col: str) -> float:
        return round(df.agg(F.sum(col)).first()[0] or 0.0, 2)

    fact_total = living(fact, "living_amount")
    untracked = fact.join(daily.select(F.col("date").alias("txn_date")), "txn_date", "left_anti").count()
    return {
        "daily_total_differs": {"severity": "error",
                                "failures": int(abs(living(daily, "living_spend") - fact_total) > 0.01)},
        "monthly_total_differs": {"severity": "error",
                                  "failures": int(abs(living(monthly, "living_spend") - fact_total) > 0.01)},
        "transaction_on_untracked_day": {"severity": "error", "failures": untracked},
    }
