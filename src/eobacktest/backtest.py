"""Backtest metrics: calibration per policy, forecast bias by lifecycle stage, cohort views."""

from __future__ import annotations

from typing import Any

import duckdb

from . import config as C
from .db import fetch_dicts, sql


def compute_backtest(con: duckdb.DuckDBPyConnection) -> None:
    """Materialise ``backtest_metrics`` and ``forecast_bias`` from the as-of tables."""
    con.execute(sql("backtest"))
    con.execute(sql("bias"))


def _ratio(num: float, den: float) -> float | None:
    return num / den if den else None


def calibration(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """One row per policy, summed over all scored quarter-ends."""
    rows = fetch_dicts(
        con,
        """
        SELECT policy,
               sum(reserve_usd) AS reserve_usd,
               sum(realized_usd) AS realized_usd,
               sum(under_reserve_usd) AS under_reserve_usd,
               sum(true_pos) AS true_pos, sum(false_pos) AS false_pos,
               sum(false_neg) AS false_neg, sum(true_neg) AS true_neg
        FROM backtest_metrics GROUP BY policy ORDER BY policy
        """,
    )
    for r in rows:
        r["coverage_ratio"] = _ratio(r["reserve_usd"], r["realized_usd"])
        r["precision"] = _ratio(r["true_pos"], r["true_pos"] + r["false_pos"])
        r["recall"] = _ratio(r["true_pos"], r["true_pos"] + r["false_neg"])
    return rows


def per_quarter(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = fetch_dicts(con, "SELECT * FROM backtest_metrics ORDER BY policy, snapshot_date")
    for r in rows:
        r["coverage_ratio"] = _ratio(r["reserve_usd"], r["realized_usd"])
        r["precision"] = _ratio(r["true_pos"], r["true_pos"] + r["false_pos"])
        r["recall"] = _ratio(r["true_pos"], r["true_pos"] + r["false_neg"])
    return rows


def forecast_bias(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    order = {stage: i for i, stage in enumerate(C.STAGES)}
    rows = fetch_dicts(con, "SELECT * FROM forecast_bias")
    return sorted(rows, key=lambda r: order.get(r["stage"], 99))


def cohort_view(con: duckdb.DuckDBPyConnection, phone_model: str) -> list[dict[str, Any]]:
    """Reserve by policy, on-hand and realized E&O for one phone model at every quarter-end.

    Realized is None for quarter-ends with fewer than 12 months of sales after them. Scrap is
    the cost of the cohort's stock scrapped in the three months after the snapshot.
    """
    return fetch_dicts(
        con,
        """
        WITH cohort AS (SELECT sku_id FROM sku WHERE phone_model = $model),
        res AS (
            SELECT snapshot_date,
                   sum(reserve_usd) FILTER (WHERE policy = 'A') AS reserve_a,
                   sum(reserve_usd) FILTER (WHERE policy = 'B') AS reserve_b,
                   sum(reserve_usd) FILTER (WHERE policy = 'C') AS reserve_c,
                   sum(reserve_usd) FILTER (WHERE policy = 'D') AS reserve_d
            FROM reserve_asof WHERE sku_id IN (SELECT sku_id FROM cohort)
            GROUP BY snapshot_date
        ),
        pos AS (
            SELECT snapshot_date, sum(on_hand_units * unit_cost) AS on_hand_usd
            FROM position_asof WHERE sku_id IN (SELECT sku_id FROM cohort)
            GROUP BY snapshot_date
        ),
        real AS (
            SELECT snapshot_date, sum(realized_usd) AS realized_usd
            FROM realized_asof WHERE sku_id IN (SELECT sku_id FROM cohort)
            GROUP BY snapshot_date
        ),
        scrapped AS (
            SELECT p.snapshot_date, sum(s.units * s.unit_cost) AS scrap_next_quarter_usd
            FROM (SELECT DISTINCT snapshot_date FROM position_asof) AS p
            JOIN scrap AS s ON s.txn_date > p.snapshot_date
                           AND s.txn_date <= p.snapshot_date + INTERVAL 3 MONTH
                           AND s.sku_id IN (SELECT sku_id FROM cohort)
            GROUP BY p.snapshot_date
        )
        SELECT res.snapshot_date, pos.on_hand_usd,
               res.reserve_a, res.reserve_b, res.reserve_c, res.reserve_d,
               real.realized_usd, coalesce(scrapped.scrap_next_quarter_usd, 0) AS scrap_usd
        FROM res JOIN pos USING (snapshot_date)
        LEFT JOIN real USING (snapshot_date)
        LEFT JOIN scrapped USING (snapshot_date)
        ORDER BY res.snapshot_date
        """,
        {"model": phone_model},
    )


def cohort_by_skus(con: duckdb.DuckDBPyConnection, sku_ids: list[str]) -> list[dict[str, Any]]:
    """Reserve and realized dollars for an explicit SKU list, summed over scored quarter-ends."""
    return fetch_dicts(
        con,
        """
        SELECT r.policy,
               sum(r.reserve_usd) AS reserve_usd,
               sum(z.realized_usd) AS realized_usd,
               sum(z.on_hand_units * z.unit_cost) AS on_hand_usd
        FROM reserve_asof AS r JOIN realized_asof AS z USING (snapshot_date, sku_id)
        WHERE r.sku_id IN (SELECT unnest($ids))
        GROUP BY r.policy ORDER BY r.policy
        """,
        {"ids": sku_ids},
    )
