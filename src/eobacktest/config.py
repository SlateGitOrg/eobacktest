"""Shared constants and calendar helpers.

Month index 1 is January 2023; index 0 is December 2022 (the opening position) and
index 36 is December 2025. Every snapshot is taken on the last day of a month.
"""

from __future__ import annotations

from datetime import date, timedelta

START_YEAR = 2023
N_MONTHS = 36
HORIZON_MONTHS = 12
USAGE_WINDOW_MONTHS = 6

# Quarter-end snapshots from month 6, the first with a full trailing-usage window in the data.
QUARTER_END_MONTHS = tuple(range(6, N_MONTHS + 1, 3))
# A snapshot is scored when all 12 forward months of sales exist.
SCORED_MONTHS = tuple(m for m in QUARTER_END_MONTHS if m + HORIZON_MONTHS <= N_MONTHS)

POLICIES = ("A", "B", "C", "D")
POLICY_NAMES = {
    "A": "Forecast coverage",
    "B": "Trailing usage",
    "C": "Aging",
    "D": "Max of B and C",
}
POLICY_DESCRIPTIONS = {
    "A": "Units beyond the 12-month forecast of the version current at the snapshot, at 100%",
    "B": "Units beyond 12 x the trailing 6-month average usage, at 100% of cost",
    "C": "No movement for more than 180 days reserves 50%; more than 365 days reserves 100%",
    "D": "The larger of the Policy B and Policy C reserve dollars",
}

AGING_HALF_DAYS = 180
AGING_FULL_DAYS = 365

# Lifecycle stage labels carried by lifecycle.csv.
STAGES = ("ramp", "mature", "decline", "dormant")


def month_start(m: int) -> date:
    """First day of month index m (1 = January of START_YEAR; 0 and below allowed)."""
    years, month0 = divmod(m - 1, 12)
    return date(START_YEAR + years, month0 + 1, 1)


def month_end(m: int) -> date:
    """Last day of month index m."""
    return month_start(m + 1) - timedelta(days=1)


def quarter_label(d: date) -> str:
    return f"{d.year} Q{(d.month - 1) // 3 + 1}"
