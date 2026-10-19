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

# Manual fixes to the running balance.
ADJUSTMENT_PATTERN = r"^correction"

# Big, non-recurring items kept out of "living spend" (issues 11, 12).
# Decision pending: tuition and hall fees only, or also anything over a threshold?
ONE_OFF_PATTERNS = [
    r"tuition",
    r"^hall fee",
]

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
