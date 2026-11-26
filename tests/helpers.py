"""Hand-built fixtures: a three-SKU position at one snapshot, with sales and forecasts."""

from __future__ import annotations

from datetime import date, timedelta

import duckdb

from eobacktest import db

SNAP = date(2024, 6, 30)


def month_starts(first: date, n: int) -> list[date]:
    out = []
    y, m = first.year, first.month
    for _ in range(n):
        out.append(date(y, m, 1))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def fixture_connection() -> duckdb.DuckDBPyConnection:
    """SKU1 / SKU2 / SKU3 as in the policy fixture, snapshot 2024-06-30."""
    con = db.connect()
    db.create_schema(con)
    snaps = [
        ("SKU1", 1200, 4.0, SNAP - timedelta(days=20)),
        ("SKU2", 500, 10.0, SNAP - timedelta(days=400)),
        ("SKU3", 300, 2.0, SNAP - timedelta(days=200)),
    ]
    for sku, on_hand, cost, last_move in snaps:
        con.execute(
            "INSERT INTO inventory_snapshot VALUES (?, ?, ?, ?, ?)",
            [SNAP, sku, on_hand, cost, last_move],
        )
    # Forecast versions dated at the snapshot: SKU1 totals 600, SKU3 totals 400, SKU2 has none.
    targets = month_starts(date(2024, 7, 1), 12)
    for sku, monthly in (("SKU1", 50.0), ("SKU3", 400 / 12)):
        for t in targets:
            con.execute("INSERT INTO forecast_version VALUES (?, ?, ?, ?)", [SNAP, sku, t, monthly])
    # Trailing six months of usage: SKU1 30 a month, SKU3 50 a month, SKU2 none.
    for t in month_starts(date(2024, 1, 1), 6):
        con.execute("INSERT INTO sales VALUES (?, 'SKU1', 30)", [t])
        con.execute("INSERT INTO sales VALUES (?, 'SKU3', 50)", [t])
    return con


def add_next_year_sales(con: duckdb.DuckDBPyConnection, sold: dict[str, int]) -> None:
    """Spread each SKU's units over the 12 months after the snapshot."""
    for sku, units in sold.items():
        base, extra = divmod(units, 12)
        for i, t in enumerate(month_starts(date(2024, 7, 1), 12)):
            con.execute(
                "INSERT INTO sales VALUES (?, ?, ?)", [t, sku, base + (1 if i < extra else 0)]
            )


def reserves_at(con: duckdb.DuckDBPyConnection, snap: date = SNAP) -> dict[tuple[str, str], float]:
    rows = con.execute(
        "SELECT sku_id, policy, reserve_usd FROM reserve_asof WHERE snapshot_date = ?", [snap]
    ).fetchall()
    return {(sku, policy): usd for sku, policy, usd in rows}
