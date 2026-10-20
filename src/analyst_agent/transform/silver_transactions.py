"""Bronze sheet rows -> silver transactions, in PySpark.

Each step is a small function that takes and returns a DataFrame, so it can be tested on its own
(tests/transform/) and read top to bottom in `build_silver`. The numbered issues refer to the
data-quality table in the project plan.

Bronze cells are JSON strings exactly as fetched: 2.5 -> '2.5', "Food" -> '"Food"', blank -> '""'.
"""

from __future__ import annotations

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F

from analyst_agent.transform import rules


# ---------------------------------------------------------------------------- helpers

def decode(cell: Column) -> Column:
    """'"Food"' -> 'Food', '2.5' -> '2.5', '""' -> null. Wrapping in {"v": ...} lets one JSON path read both."""
    text = F.get_json_object(F.concat(F.lit('{"v":'), cell, F.lit("}")), "$.v")
    return F.nullif(F.trim(text), F.lit(""))


def try_double(name: str) -> Column:
    # try_cast, not cast: serverless runs in ANSI mode, where a bad cast fails the job instead of giving null
    return F.expr(f"try_cast(`{name}` AS DOUBLE)")


def make_date(y: Column, m: Column, d: Column) -> Column:
    """Like make_date, but returns null for impossible dates (month 13, 30 Feb) instead of failing."""
    text = F.format_string("%04d-%02d-%02d", y, m, d)
    return F.to_date(F.call_function("try_to_timestamp", text, F.lit("yyyy-MM-dd")))


def normalise_header(cell: Column) -> Column:
    return F.regexp_replace(F.lower(decode(cell)), r"[^a-z0-9]", "")


# ---------------------------------------------------------------------------- steps

def latest_snapshot(bronze: DataFrame) -> DataFrame:
    """Every ingest appends a full copy of the sheet; silver is rebuilt from the newest one."""
    newest = bronze.agg(F.max("snapshot_file").alias("snapshot_file"))
    return bronze.join(newest, "snapshot_file")


def month_tabs(df: DataFrame) -> DataFrame:
    """Issue 1: keep month tabs ("8/25"), drop templates, and read the month and year from the name."""
    return (
        df.filter(F.col("tab").rlike(rules.MONTH_TAB_PATTERN))
        .withColumn("tab_month", F.regexp_extract("tab", rules.MONTH_TAB_PATTERN, 1).cast("int"))
        .withColumn("tab_year", (F.regexp_extract("tab", rules.MONTH_TAB_PATTERN, 2).cast("int") + 2000))
        .withColumn("tab_start", make_date(F.col("tab_year"), F.col("tab_month"), F.lit(1)))
    )


def named_columns(df: DataFrame) -> DataFrame:
    """Issue 2: columns move between tabs, so find each one by its header text, not its position."""
    headers = (
        df.filter(F.col("row_num") == 1)
        .select("tab", F.transform("formatted", normalise_header).alias("headers"))
    )
    rows = df.filter(F.col("row_num") > 1).join(headers, "tab")

    for name, aliases in rules.HEADER_ALIASES.items():
        positions = [F.array_position("headers", a) for a in aliases]
        position = F.greatest(*positions) if len(positions) > 1 else positions[0]  # 1-based, 0 = missing
        index = (position - 1).cast("int")
        rows = (
            rows.withColumn(f"{name}_cell", F.when(position > 0, F.get("raw", index)))
            .withColumn(f"{name}_formula", F.when(position > 0, F.get("formula", index)))
            .withColumn(name, decode(F.col(f"{name}_cell")))
        )
    return rows.drop("headers")


def resolve_dates(df: DataFrame) -> DataFrame:
    """Issues 3 and 4: fix day/month swaps, parse dates typed as text, then fill each date down.

    A date cell is either a serial number (days since 1899-12-30) or text like "13/1" (day/month).
    Two readings are tried: as stored, and with day and month swapped. The first that falls inside
    the tab's month wins. Failing that, the previous month is accepted, because tabs can start a few
    days early (8/25 starts on 24 Jul).
    """
    is_serial = F.col("date_cell").rlike(r"^[0-9]+(\.[0-9]+)?$")
    serial = F.date_add(F.lit("1899-12-30").cast("date"), F.floor(F.col("date").cast("double")).cast("int"))
    text_day = F.regexp_extract("date", r"^(\d{1,2})/(\d{1,2})", 1).cast("int")
    text_month = F.regexp_extract("date", r"^(\d{1,2})/(\d{1,2})", 2).cast("int")

    y = F.when(is_serial, F.year(serial)).otherwise(F.col("tab_year"))
    m = F.when(is_serial, F.month(serial)).otherwise(text_month)
    d = F.when(is_serial, F.dayofmonth(serial)).otherwise(text_day)
    as_is, swapped = make_date(y, m, d), make_date(y, d, m)

    start, next_start = F.col("tab_start"), F.add_months(F.col("tab_start"), 1)
    prev_start = F.add_months(F.col("tab_start"), -1)

    def in_month(c: Column) -> Column:
        return (c >= start) & (c < next_start)

    def in_prev(c: Column) -> Column:
        return (c >= prev_start) & (c < start)

    resolved = (
        F.when(in_month(as_is), F.struct(as_is.alias("d"), F.lit("as_is").alias("how")))
        .when(in_month(swapped), F.struct(swapped.alias("d"), F.lit("swapped").alias("how")))
        .when(in_prev(as_is), F.struct(as_is.alias("d"), F.lit("previous_month").alias("how")))
        .when(in_prev(swapped), F.struct(swapped.alias("d"), F.lit("previous_month_swapped").alias("how")))
    )
    resolved = F.when(F.col("date").isNotNull(), resolved)

    fill_down = Window.partitionBy("tab").orderBy("row_num").rowsBetween(Window.unboundedPreceding, 0)
    return (
        df.withColumn("_resolved", resolved)
        .withColumn("date_fix", F.when(F.col("_resolved").isNotNull(),
                                       F.concat(F.when(is_serial, F.lit("serial:")).otherwise(F.lit("text:")),
                                                F.col("_resolved.how"))))
        .withColumn("txn_date", F.last(F.col("_resolved.d"), ignorenulls=True).over(fill_down))
        .withColumn("date_fix", F.last("date_fix", ignorenulls=True).over(fill_down))
        .drop("_resolved")
    )


def sheet_exclusions(df: DataFrame) -> DataFrame:
    """Issue 11: rows the sheet's Total/Normal formulas subtract by hand, e.g. =SUM(E2:E147)-E110-E74.

    Kept as a flag for comparison only. It is not trusted as the one-off rule, because formulas get
    copy-pasted between months with the old row numbers still in them.
    """
    summary = df.filter(F.col("item").isNull() & F.col("category").isNull()).select(
        "tab", F.explode(F.array(*[F.col(f"{c}_formula") for c in ("expense", "income", "category")])).alias("f")
    )
    summary = summary.withColumn("f", decode(F.col("f"))).filter(F.lower("f").rlike(r"^=\s*sum\(.*\)\s*-"))
    refs = summary.select(
        "tab", F.explode(F.regexp_extract_all("f", F.lit(r"-\s*\$?[A-Za-z]+\$?(\d+)"), 1)).alias("ref")
    )
    return refs.select("tab", F.col("ref").cast("int").alias("row_num")).distinct()


def clean_items(df: DataFrame) -> DataFrame:
    """Issue 10: 'Air-Con ', 'air con' -> 'air con'. A real alias table comes later."""
    return df.withColumn(
        "item_clean", F.trim(F.regexp_replace(F.lower("item"), r"[\s\-_]+", " "))
    )


def to_transactions(df: DataFrame) -> DataFrame:
    """Issues 5, 6, 7: drop summary rows, read real numbers, split each row into money in / money out.

    A sheet row can carry both an income and an expense (a split bill paid back), so it can become
    two transactions. Balance carry-over rows become `opening_balance`, not income.
    """
    # Summary rows (Total, Avg, Normal) have no item and no category. A row with a category but a
    # blank item is a real purchase whose name was never typed, so it stays.
    has_category = F.col("category").isin(*rules.EXPENSE_CATEGORIES, rules.INCOME_CATEGORY)
    rows = df.filter(F.col("item").isNotNull() | has_category)
    income, expense, remaining = try_double("income"), try_double("expense"), try_double("remaining")

    is_opening = F.col("item_clean").rlike(rules.OPENING_BALANCE_PATTERN)
    is_income_cat = F.col("category") == rules.INCOME_CATEGORY
    opening_amount = F.coalesce(F.when(income > 0, income), remaining)

    legs = F.when(
        is_opening,
        F.array(F.struct(F.lit("in").alias("direction"), opening_amount.alias("amount_sgd"))),
    ).otherwise(
        F.filter(
            F.array(
                F.struct(F.lit("in").alias("direction"), income.alias("amount_sgd")),
                F.struct(F.lit("out").alias("direction"), expense.alias("amount_sgd")),
            ),
            lambda leg: leg["amount_sgd"] > 0,
        )
    )
    txns = rows.withColumn("leg", F.explode(legs)).select("*", "leg.*").drop("leg")

    kind = (
        F.when(is_opening, "opening_balance")
        .when(F.col("item_clean").rlike(rules.ADJUSTMENT_PATTERN), "adjustment")
        .when((F.col("direction") == "out"), "expense")
        .when(F.col("item_clean").rlike(rules.REFUND_PATTERN), "refund")
        .when(is_income_cat, "income")
        .otherwise("refund")  # money in on an expense row: someone paying back their share
    )
    return txns.withColumn("txn_kind", kind)


def fix_categories(df: DataFrame) -> DataFrame:
    """Issue 9: an item's most common category wins over one-off typos, then explicit overrides apply."""
    expenses = F.col("txn_kind") == "expense"
    category_raw = F.when(expenses & (F.col("category") != rules.INCOME_CATEGORY), F.col("category"))
    df = df.withColumn("category_raw", F.col("category")).withColumn("category", category_raw)

    votes = (
        df.filter(expenses & F.col("category").isNotNull())
        .groupBy("item_clean", "category").count()
        .withColumn("item_rows", F.sum("count").over(Window.partitionBy("item_clean")))
        .withColumn("rank", F.row_number().over(
            Window.partitionBy("item_clean").orderBy(F.desc("count"), F.col("category"))))
        .filter((F.col("rank") == 1)
                & (F.col("item_rows") >= rules.CATEGORY_VOTE_MIN_ROWS)
                & (F.col("count") * 2 > F.col("item_rows")))
        .select("item_clean", F.col("category").alias("voted_category"))
    )
    df = df.join(votes, "item_clean", "left")

    category = F.when(expenses, F.coalesce("voted_category", "category"))
    for pattern, forced in rules.CATEGORY_OVERRIDES.items():
        category = F.when(expenses & F.col("item_clean").rlike(pattern), F.lit(forced)).otherwise(category)
    return df.withColumn("category", category).drop("voted_category")


def flag_one_offs(df: DataFrame, exclusions: DataFrame) -> DataFrame:
    """Issues 11, 12: `is_one_off` from explicit rules; `excluded_in_sheet` from the sheet's formulas."""
    pattern = "|".join(f"({p})" for p in rules.ONE_OFF_PATTERNS)
    flagged = exclusions.withColumn("excluded_in_sheet", F.lit(True))
    return (
        df.withColumn("is_one_off", F.coalesce(F.col("item_clean").rlike(pattern), F.lit(False)))
        .join(flagged, ["tab", "row_num"], "left")
        .withColumn("excluded_in_sheet", F.coalesce("excluded_in_sheet", F.lit(False)))
    )


def flag_reimbursable(df: DataFrame) -> DataFrame:
    """Work money I pay out and get back, listed by row in rules.py or marked in the Note column."""
    marked = F.lower(F.coalesce(F.col("note"), F.lit(""))).rlike(rules.REIMBURSABLE_NOTE_PATTERN)
    for tab, rows in rules.REIMBURSABLE_ROWS.items():
        marked = marked | ((F.col("tab") == tab) & F.col("row_num").isin(rows))
    return df.withColumn("is_reimbursable", marked)


def study_terms(df: DataFrame) -> DataFrame:
    """Study year (1 = Aug 2025 to May 2026) and semester (1 = Aug-Dec, 2 = Jan-May) from the date."""
    month = F.month("txn_date")
    semester = (F.when(month.isin(rules.SEMESTER_1_MONTHS), F.lit(1))
                .when(month.isin(rules.SEMESTER_2_MONTHS), F.lit(2)))
    year_start = F.year("txn_date") - F.when(month < min(rules.SEMESTER_1_MONTHS), 1).otherwise(0)
    study_year = F.when(semester.isNotNull(), year_start - rules.FIRST_STUDY_YEAR + 1)
    return (
        df.withColumn("study_year", study_year)
        .withColumn("semester", semester)
        .withColumn("term", F.when(semester.isNotNull(), F.format_string("Y%dS%d", "study_year", "semester")))
    )


def build_silver(bronze: DataFrame) -> DataFrame:
    rows = named_columns(month_tabs(latest_snapshot(bronze)))
    rows = resolve_dates(rows)
    exclusions = sheet_exclusions(rows)

    txns = to_transactions(clean_items(rows))
    txns = flag_one_offs(fix_categories(txns), exclusions)
    txns = study_terms(flag_reimbursable(txns))

    txn_id = F.sha2(F.concat_ws("|", "tab", "row_num", "direction", "item", F.col("amount_sgd").cast("string")), 256)
    return txns.select(
        F.substring(txn_id, 1, 16).alias("txn_id"),
        "txn_date",
        F.col("item").alias("item_raw"),
        "item_clean",
        "category",
        "category_raw",
        F.round("amount_sgd", 2).alias("amount_sgd"),
        "direction",
        "txn_kind",
        "payment_method",
        "note",
        "is_one_off",
        "is_reimbursable",
        "excluded_in_sheet",
        "study_year",
        "semester",
        "term",
        "date_fix",
        F.col("tab").alias("source_tab"),
        F.col("row_num").alias("source_row"),
        "snapshot_file",
        F.current_timestamp().alias("loaded_at"),
    )


# ---------------------------------------------------------------------------- data tests

def data_tests(silver: DataFrame) -> dict[str, dict]:
    """Returns {test_name: {"severity": "error"|"warn", "failures": n}}. Errors stop the job."""
    tab_start = F.to_date(F.concat(F.lit("20"), F.split("source_tab", "/")[1], F.lit("-"),
                                   F.lpad(F.split("source_tab", "/")[0], 2, "0"), F.lit("-01")))
    checks = {
        "date_missing": ("error", F.col("txn_date").isNull()),
        "date_outside_tab_month": ("error", (F.col("txn_date") < F.add_months(tab_start, -1))
                                   | (F.col("txn_date") >= F.add_months(tab_start, 1))),
        "amount_not_positive": ("error", ~F.coalesce(F.col("amount_sgd") > 0, F.lit(False))),
        "outside_semester": ("warn", F.col("semester").isNull()),
        "expense_category_unknown": ("warn", (F.col("txn_kind") == "expense")
                                     & ~F.coalesce(F.col("category").isin(rules.EXPENSE_CATEGORIES), F.lit(False))),
        "category_changed_by_cleaning": ("warn", (F.col("txn_kind") == "expense")
                                         & ~F.col("category").eqNullSafe(F.col("category_raw"))),
    }
    counts = silver.agg(
        *[F.sum(F.when(cond, 1).otherwise(0)).alias(name) for name, (_, cond) in checks.items()],
        (F.count("*") - F.countDistinct("txn_id")).alias("txn_id_duplicated"),
    ).first().asDict()

    severities = {name: sev for name, (sev, _) in checks.items()} | {"txn_id_duplicated": "error"}
    return {name: {"severity": severities[name], "failures": int(counts[name] or 0)} for name in severities}
