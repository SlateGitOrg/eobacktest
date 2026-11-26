"""The metric registry: the measure names, their DAX and the SQL reference for each.

The Power BI model must define exactly these measures (``validate-pbip`` checks the set), and
``tests/test_pbip.py`` evaluates each DAX expression over the exported CSVs and compares it with
the reference SQL below.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Measure:
    name: str
    table: str  # table the measure is defined on
    dax: str
    format: str
    reference_sql: str  # one-row, one-column query over the as-of tables


_CUR = "\\$#,0"
_RATIO = "0.000"
_PCT = "0.0%"
_MONTHS = "0.0"

MEASURES: tuple[Measure, ...] = (
    Measure(
        "Reserve $",
        "fct_reserve",
        "SUM(fct_reserve[reserve_usd])",
        _CUR,
        "SELECT sum(reserve_usd) FROM reserve_asof",
    ),
    Measure(
        "Realized E&O $",
        "fct_realized",
        "SUM(fct_realized[realized_usd])",
        _CUR,
        "SELECT sum(realized_usd) FROM realized_asof",
    ),
    Measure(
        "Coverage Ratio",
        "fct_backtest",
        "DIVIDE(SUM(fct_backtest[reserve_usd]), SUM(fct_backtest[realized_usd]))",
        _RATIO,
        "SELECT sum(reserve_usd) / sum(realized_usd) FROM backtest_metrics",
    ),
    Measure(
        "Under-reserve $",
        "fct_backtest",
        "SUM(fct_backtest[under_reserve_usd])",
        _CUR,
        "SELECT sum(under_reserve_usd) FROM backtest_metrics",
    ),
    Measure(
        "Flag Precision",
        "fct_backtest",
        "DIVIDE(SUM(fct_backtest[true_pos]), "
        "SUM(fct_backtest[true_pos]) + SUM(fct_backtest[false_pos]))",
        _PCT,
        "SELECT sum(true_pos) / (sum(true_pos) + sum(false_pos)) FROM backtest_metrics",
    ),
    Measure(
        "Flag Recall",
        "fct_backtest",
        "DIVIDE(SUM(fct_backtest[true_pos]), "
        "SUM(fct_backtest[true_pos]) + SUM(fct_backtest[false_neg]))",
        _PCT,
        "SELECT sum(true_pos) / (sum(true_pos) + sum(false_neg)) FROM backtest_metrics",
    ),
    Measure(
        "Months of Supply (Forecast)",
        "fct_realized",
        "DIVIDE(SUM(fct_realized[on_hand_units]), SUM(fct_realized[forecast_12m_units]) / 12)",
        _MONTHS,
        "SELECT sum(on_hand_units) / (sum(forecast_12m_units) / 12) FROM position_asof",
    ),
    Measure(
        "Months of Supply (Trailing)",
        "fct_realized",
        "DIVIDE(SUM(fct_realized[on_hand_units]), SUM(fct_realized[trailing_avg_usage]))",
        _MONTHS,
        "SELECT sum(on_hand_units) / sum(trailing_avg_usage) FROM position_asof",
    ),
)

MEASURE_BY_NAME = {m.name: m for m in MEASURES}
MEASURE_NAMES = frozenset(MEASURE_BY_NAME)
