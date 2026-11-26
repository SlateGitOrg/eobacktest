from __future__ import annotations

from datetime import date, timedelta

import pytest
from helpers import SNAP, fixture_connection, month_starts, reserves_at

from eobacktest import db


def test_lookahead_assertion_returns_zero_rows_on_generated_data(built):
    assert db.lookahead_rows(built) == []
    db.assert_no_lookahead(built)


def test_forecast_version_dated_after_the_snapshot_leaves_reserves_unchanged():
    con = fixture_connection()
    db.compute_position(con, SNAP)
    db.compute_reserves(con, SNAP)
    before = reserves_at(con)
    for sku in ("SKU1", "SKU2", "SKU3"):
        for t in month_starts(date(2024, 8, 1), 12):
            con.execute(
                "INSERT INTO forecast_version VALUES (?, ?, ?, 0)",
                [SNAP + timedelta(days=1), sku, t],
            )
    for table in ("position_asof", "reserve_asof"):
        con.execute(f"DELETE FROM {table}")
    db.compute_position(con, SNAP)
    db.compute_reserves(con, SNAP)
    assert reserves_at(con) == before
    db.assert_no_lookahead(con)


def test_latest_version_at_or_before_the_snapshot_wins():
    con = fixture_connection()
    older = SNAP - timedelta(days=30)
    for t in month_starts(date(2024, 6, 1), 12):
        con.execute("INSERT INTO forecast_version VALUES (?, 'SKU1', ?, 100)", [older, t])

    def sku1_forecast() -> float:
        con.execute("DELETE FROM position_asof")
        db.compute_position(con, SNAP)
        (units,) = con.execute(
            "SELECT forecast_12m_units FROM position_asof WHERE sku_id = 'SKU1'"
        ).fetchone()
        return units

    assert sku1_forecast() == pytest.approx(600.0)  # the version dated on the snapshot day
    con.execute("DELETE FROM forecast_version WHERE sku_id = 'SKU1' AND version_date = ?", [SNAP])
    assert sku1_forecast() == pytest.approx(1200.0)  # falls back to the older version


def test_planted_future_version_never_reaches_a_snapshot(data_dir, truth):
    planted = truth["planted_future_version"]
    assert planted["version_date"] == "2026-02-15" and len(planted["skus"]) == 12

    def reserves_without(remove_planted: bool):
        con = db.connect()
        db.create_schema(con)
        db.load_csvs(con, data_dir)
        if remove_planted:
            con.execute("DELETE FROM forecast_version WHERE version_date = DATE '2026-02-15'")
        db.build(con)
        return con.execute(
            "SELECT snapshot_date, sku_id, policy, reserve_usd FROM reserve_asof ORDER BY ALL"
        ).fetchall()

    assert reserves_without(False) == reserves_without(True)


def test_naive_latest_version_join_is_caught_by_the_guard(data_dir, truth):
    """Dropping the version_date filter lets the planted zero forecast reach every snapshot."""
    con = db.connect()
    db.create_schema(con)
    db.load_csvs(con, data_dir)
    leaky = db.sql("position").replace("WHERE version_date <= $snap::DATE", "WHERE TRUE")
    assert leaky != db.sql("position")
    snap = date(2024, 6, 30)
    db.compute_position(con, snap, text=leaky)
    rows = db.lookahead_rows(con)
    assert len(rows) >= 12
    leaked = {r[1] for r in rows}
    assert set(truth["planted_future_version"]["skus"]) <= leaked
    with pytest.raises(db.LookaheadError, match="lookahead rows"):
        db.assert_no_lookahead(con)


def test_leaky_join_would_flatter_policy_a_on_the_planted_skus(data_dir, truth):
    skus = truth["planted_future_version"]["skus"]

    def policy_a_reserve(text: str | None) -> float:
        con = db.connect()
        db.create_schema(con)
        db.load_csvs(con, data_dir)
        snap = date(2024, 12, 31)
        db.compute_position(con, snap, text=text)
        db.compute_reserves(con, snap)
        (usd,) = con.execute(
            "SELECT sum(reserve_usd) FROM reserve_asof WHERE policy = 'A' "
            "AND sku_id IN (SELECT unnest($ids))",
            {"ids": skus},
        ).fetchone()
        return usd

    honest = policy_a_reserve(None)
    leaky = policy_a_reserve(
        db.sql("position").replace("WHERE version_date <= $snap::DATE", "WHERE TRUE")
    )
    assert leaky > 2 * honest
    # With a zero forecast every unit on hand is reserved under Policy A.
    con = db.connect()
    db.create_schema(con)
    db.load_csvs(con, data_dir)
    (value,) = con.execute(
        "SELECT sum(on_hand_units * unit_cost) FROM inventory_snapshot "
        "WHERE snapshot_date = DATE '2024-12-31' AND sku_id IN (SELECT unnest($ids))",
        {"ids": skus},
    ).fetchone()
    assert leaky == pytest.approx(value)
