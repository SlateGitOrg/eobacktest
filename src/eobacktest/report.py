"""Markdown backtest report: calibration per policy and the end-of-life cohort views."""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb

from . import backtest as bt
from . import config as C


def money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.0f}"


def ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def calibration_table(con: duckdb.DuckDBPyConnection) -> str:
    rows = [
        [
            r["policy"],
            money(r["reserve_usd"]),
            money(r["realized_usd"]),
            ratio(r["coverage_ratio"]),
            money(r["under_reserve_usd"]),
            pct(r["precision"]),
            pct(r["recall"]),
        ]
        for r in bt.calibration(con)
    ]
    return table(
        [
            "Policy",
            "Reserve $",
            "Realized $",
            "Coverage ratio",
            "Under-reserve $",
            "Precision",
            "Recall",
        ],
        rows,
    )


def quarter_tables(con: duckdb.DuckDBPyConnection) -> str:
    by_policy: dict[str, list[dict[str, Any]]] = {}
    for r in bt.per_quarter(con):
        by_policy.setdefault(r["policy"], []).append(r)
    blocks = []
    for policy in C.POLICIES:
        rows = [
            [
                r["snapshot_date"].isoformat(),
                money(r["reserve_usd"]),
                money(r["realized_usd"]),
                ratio(r["coverage_ratio"]),
                money(r["under_reserve_usd"]),
                pct(r["precision"]),
                pct(r["recall"]),
            ]
            for r in by_policy.get(policy, [])
        ]
        blocks.append(
            f"### Policy {policy}: {C.POLICY_NAMES[policy]}\n\n"
            + table(
                [
                    "Quarter-end",
                    "Reserve $",
                    "Realized $",
                    "Coverage ratio",
                    "Under-reserve $",
                    "Precision",
                    "Recall",
                ],
                rows,
            )
        )
    return "\n\n".join(blocks)


def bias_table(con: duckdb.DuckDBPyConnection) -> str:
    rows = [
        [
            r["stage"],
            f"{r['n']:,}",
            f"{r['forecast_units']:,.0f}",
            f"{r['actual_units']:,.0f}",
            "n/a (no sales)" if r["bias"] is None else f"{r['bias'] * 100:+.1f}%",
        ]
        for r in bt.forecast_bias(con)
    ]
    return table(["Lifecycle stage", "SKU-months", "Forecast units", "Actual units", "Bias"], rows)


def cohort_table(con: duckdb.DuckDBPyConnection, phone_model: str) -> str:
    rows = [
        [
            r["snapshot_date"].isoformat(),
            money(r["on_hand_usd"]),
            money(r["reserve_a"]),
            money(r["reserve_b"]),
            money(r["reserve_c"]),
            money(r["reserve_d"]),
            money(r["realized_usd"]),
            money(r["scrap_usd"]),
        ]
        for r in bt.cohort_view(con, phone_model)
    ]
    return table(
        [
            "Quarter-end",
            "On hand $",
            "Reserve A",
            "Reserve B",
            "Reserve C",
            "Reserve D",
            "Realized E&O $",
            "Scrapped next quarter $",
        ],
        rows,
    )


def discontinuation(con: duckdb.DuckDBPyConnection, model: str) -> date:
    row = con.execute(
        "SELECT discontinued_month FROM phone_model WHERE model_id = $m", {"m": model}
    ).fetchone()
    assert row is not None and row[0] is not None
    return row[0]


def render_report(con: duckdb.DuckDBPyConnection) -> str:
    bt.compute_backtest(con)
    parts = [
        "# E&O reserve backtest",
        "Each policy is replayed at every quarter-end from the stock, sales and forecast version "
        "known on that date and scored against stock still unsold 12 months later. Realized E&O "
        "needs 12 forward months, so the last scored quarter-end is "
        f"{C.month_end(C.SCORED_MONTHS[-1]).isoformat()}; later quarter-ends show reserves only.",
        "## Calibration by policy, all scored quarter-ends",
        calibration_table(con),
        "## Calibration by quarter-end",
        quarter_tables(con),
        "## Forecast bias by lifecycle stage",
        "Bias is the sum of forecast units over the sum of sales units, minus one, for every "
        "monthly forecast version against the month it predicted, grouped by the lifecycle "
        "stage of the target month.",
        bias_table(con),
    ]
    for model in ("P-07", "P-09"):
        month = discontinuation(con, model)
        parts += [
            f"## End-of-life cohort {model} (discontinued {month.strftime('%B %Y')})",
            cohort_table(con, model),
        ]
    return "\n\n".join(parts) + "\n"
