"""Business rules for cleaning the money-tracker sheet. Edit these, not the transform code.

Patterns are regular expressions matched against `item_clean` (lower-case, single spaces).
Keep names of people OUT of this file: the repo is public.
"""

# Tabs named like "8/25" (month/2-digit year). Anything else (EMPTY, EMPTY2) is a template and skipped.
MONTH_TAB_PATTERN = r"^(\d{1,2})/(\d{2})$"

# Header text -> canonical column. Headers are compared after lower-casing and dropping everything
# that isn't a letter or digit, so "Remaining (SGD)" and "remaining(sgd)" both become "remainingsgd".
# THB and "Daily Expense" columns are derived in the sheet, so they are deliberately not read (issue 8, 13).
HEADER_ALIASES = {
    "date": ["date"],
    "item": ["list"],
    "payment_method": ["paymentmethod"],
    "category": ["type"],
    "income": ["incomesgd", "income"],
    "expense": ["expensesgd"],
    "remaining": ["remainingsgd"],
    "note": ["note"],
}

# The sheet's marker for money coming in.
INCOME_CATEGORY = "*Income*"

# Balance carried over from the previous month (issue 7). Not real income.
OPENING_BALANCE_PATTERN = r"^(start|remaining|monthly start)\b"

# Money coming back for something already paid.
REFUND_PATTERN = r"reimburse|refund|cash ?back"

# Manual fixes to the running balance: one "Correction" row whenever the sheet and the bank disagree.
ADJUSTMENT_PATTERN = r"^correction"

# Money paid for work (club / hall / committee) and the money paid back for it. Not personal spend or
# income. Item names alone can't tell it apart (and many contain people's names), so it's marked by hand:
#   - past rows: listed below by tab and sheet row, carried over from the Power BI file (Is_Reimbursable)
#   - new rows: type "reimb" in the row's Note cell (any capitals, e.g. "Reimb - CNY")
# Rows in closed months don't move, so the row numbers stay valid. Re-check them if an old tab is edited.
REIMBURSABLE_NOTE_PATTERN = r"reimb"
REIMBURSABLE_ROWS = {
    "9/25": [37],
    "10/25": [7, 25, 32, 33, 49, 68, 85, 87, 112, 116],
    "11/25": [51],
    "1/26": [3, 4, 6, 12, 26, 51, 56],
    "2/26": [44, 53],
    "3/26": [15, 16, 72, 73, 74],
}

# Big, non-recurring items kept out of "living spend" (issues 11, 12).
# Decision pending: tuition and hall fees only, or also anything over a threshold?
ONE_OFF_PATTERNS = [
    r"tuition",
    r"^hall fee",
]

# Balance check (phase 1 gate): each tab's closing balance is recomputed from the transactions and compared
# with the sheet's own Remaining column. Gaps below are sheet formula errors that were found and explained
# (sheet minus recomputed, S$). A gap that isn't listed, or doesn't match, fails the check.
BALANCE_TOLERANCE = 0.01
KNOWN_BALANCE_DRIFT = {
    "9/25": (-69.30, "row 3 never adds the S$66.80 cash (=H2); row 32 subtracts F33 instead of F32"),
    "10/25": (26.88, "rows 60-61 never subtract the expense (=H59+E60)"),
    "11/25": (11.25, "rows 60-61 never subtract the expense; the 20 Nov Correction (S$11.25) patches it"),
    "1/26": (19.63, "row 22 starts from row 20, skipping row 21; row 88 copies row 87 (=H87)"),
    "5/26": (2.50, "row 22 starts from row 20, skipping row 21; the 8 May Correction (S$2.50) patches it"),
    "8/26": (28.22, "rows 74 and 110 copy the row above; rows 78 and 111 ignore their income"),
}

# Study year and semester. Year 1 starts in Aug 2025 (class of 2029). Only Singapore spending is tracked;
# the rest of the year is spent in Thailand. July counts as semester 1, because I fly back a few days
# before it starts (24 Jul 2025, 27 Jul 2026) and those days sit in the August tab or a short July tab.
FIRST_STUDY_YEAR = 2025
SEMESTER_1_MONTHS = [7, 8, 9, 10, 11, 12]
SEMESTER_2_MONTHS = [1, 2, 3, 4, 5]

# Categories an expense may have after cleaning.
EXPENSE_CATEGORIES = ["Food", "Travel", "Laundry", "Air-Con", "Internet", "Haircut", "Others"]

# Explicit category overrides, applied after the majority vote (issue 9).
CATEGORY_OVERRIDES = {
    r"^hall fee": "Others",
    r"^singtel": "Internet",
    r"^haircut": "Haircut",  # older months filed haircuts under Others, before the category existed
}

# Majority vote (issue 9): an item seen at least this many times takes its most common category,
# as long as that category has more than half of the votes.
CATEGORY_VOTE_MIN_ROWS = 3
