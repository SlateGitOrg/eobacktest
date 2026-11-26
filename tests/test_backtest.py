from __future__ import annotations

from datetime import date

import pytest
from helpers import SNAP, add_next_year_sales, fixture_connection

from eobacktest import backtest, db
from eobacktest import config as C


def _cohort(built, sku_ids, policy, snap):
    return built.execute(
        """
        SELECT sum(r.reserve_usd), sum(z.realized_usd)
        FROM reserve_asof AS r JOIN realized_asof AS z USING (snapshot_date, sku_id)
        WHERE r.policy = $p AND r.snapshot_date = $d AND r.sku_id IN (SELECT unnest($ids))
        """,
        {"p": policy, "d": snap, "ids": sku_ids},
    ).fetchone()


def test_second_quarter_end_after_p07_discontinuation_policy_a_reserves_under_half(built, truth):
    ids = truth["cohorts"]["P-07"]
    snap = date(2024, 12, 31)  # discontinued month 20; quarter-ends 21 and 24 follow
    reserve_a, realized = _cohort(built, ids, "A", snap)
    reserve_b, _ = _cohort(built, ids, "B", snap)
    assert realized > 50_000
    assert reserve_a < 0.5 * realized
    assert reserve_b > reserve_a


def test_first_quarter_end_after_p07_discontinuation_every_policy_misses_the_write_off(
    built, truth
):
    ids = truth["cohorts"]["P-07"]
    reserve_d, realized = _cohort(built, ids, "D", date(2024, 9, 30))
    assert realized > 50_000
    assert reserve_d < 0.05 * realized


def test_dormant_skus_policy_a_reserves_at_most_a_quarter_and_policy_b_at_least_90_percent(
    built, truth
):
    rows = {r["policy"]: r for r in backtest.cohort_by_skus(built, truth["dormant_skus"])}
    realized = rows["A"]["realized_usd"]
    assert realized > 0
    assert rows["A"]["reserve_usd"] <= 0.25 * realized
    assert rows["B"]["reserve_usd"] >= 0.90 * realized


def test_gift_packs_at_june_snapshots_are_reserved_by_aging_but_never_go_unsold(built, truth):
    ids = truth["gift_pack_skus"]
    for snap in (date(2023, 6, 30), date(2024, 6, 30)):
        reserved = built.execute(
            "SELECT count(*) FROM reserve_asof WHERE policy = 'C' AND reserve_usd > 0 "
            "AND snapshot_date = $d AND sku_id IN (SELECT unnest($ids))",
            {"d": snap, "ids": ids},
        ).fetchone()[0]
        realized, on_hand_value = built.execute(
            "SELECT sum(realized_usd), sum(on_hand_units * unit_cost) FROM realized_asof "
            "WHERE snapshot_date = $d AND sku_id IN (SELECT unnest($ids))",
            {"d": snap, "ids": ids},
        ).fetchone()
        assert reserved >= 20
        assert on_hand_value > 0
        assert realized <= 0.05 * on_hand_value


def test_policy_d_has_the_lowest_total_under_reserve(built):
    under = {r["policy"]: r["under_reserve_usd"] for r in backtest.calibration(built)}
    assert under["D"] < under["A"]
    assert under["D"] < under["C"]
    assert under["D"] <= under["B"] + 1e-6
    assert under["D"] <= min(under.values()) + 1e-6


def test_policy_d_never_reserves_less_than_b_or_c_at_any_quarter_end(built):
    rows = built.execute(
        "SELECT snapshot_date, "
        "max(reserve_usd) FILTER (WHERE policy = 'B'), "
        "max(reserve_usd) FILTER (WHERE policy = 'C'), "
        "max(reserve_usd) FILTER (WHERE policy = 'D') FROM ("
        "SELECT snapshot_date, policy, sum(reserve_usd) AS reserve_usd FROM reserve_asof "
        "GROUP BY ALL) GROUP BY snapshot_date"
    ).fetchall()
    assert len(rows) == 11
    for _, b, c, d in rows:
        assert d >= max(b, c) - 1e-6


def test_forecast_bias_is_within_five_percent_when_mature_and_over_100_percent_in_decline(built):
    bias = {r["stage"]: r["bias"] for r in backtest.forecast_bias(built)}
    assert abs(bias["mature"]) <= 0.05
    assert bias["decline"] >= 1.0
    assert bias["ramp"] > 0
    assert bias["dormant"] is None  # nothing sold, so a ratio is undefined


def test_only_quarter_ends_with_twelve_forward_months_are_scored(built):
    scored = [
        r[0]
        for r in built.execute(
            "SELECT DISTINCT snapshot_date FROM realized_asof ORDER BY 1"
        ).fetchall()
    ]
    assert scored == [C.month_end(m) for m in C.SCORED_MONTHS]
    assert len(scored) == 7
    reserved = built.execute("SELECT count(DISTINCT snapshot_date) FROM reserve_asof").fetchone()[0]
    assert reserved == 11


def test_flag_counts_cover_every_sku_at_each_quarter_end(built):
    rows = built.execute(
        "SELECT snapshot_date, policy, true_pos + false_pos + false_neg + true_neg "
        "FROM backtest_metrics"
    ).fetchall()
    for snap, _, total in rows:
        (n,) = built.execute(
            "SELECT count(*) FROM realized_asof WHERE snapshot_date = ?", [snap]
        ).fetchone()
        assert total == n


def test_metrics_on_a_hand_built_fixture():
    con = fixture_connection()
    # Next 12 months: SKU1 sells 400 (800 units unsold = $3,200), SKU2 nothing ($5,000), SKU3 400.
    add_next_year_sales(con, {"SKU1": 400, "SKU3": 400})
    db.compute_position(con, SNAP)
    db.compute_reserves(con, SNAP)
    db.compute_realized(con, SNAP)
    con.execute(db.sql("backtest"))
    m = {r["policy"]: r for r in db.fetch_dicts(con, "SELECT * FROM backtest_metrics")}
    assert m["A"]["realized_usd"] == pytest.approx(8200.0)
    assert m["A"]["reserve_usd"] == pytest.approx(7400.0)
    assert m["A"]["under_reserve_usd"] == pytest.approx(800.0)  # SKU1 only: 3200 - 2400
    assert (m["A"]["true_pos"], m["A"]["false_pos"], m["A"]["false_neg"]) == (2, 0, 0)
    assert m["C"]["under_reserve_usd"] == pytest.approx(3200.0)  # SKU1 has no aging reserve
    assert (m["C"]["true_pos"], m["C"]["false_pos"], m["C"]["false_neg"]) == (1, 1, 1)
    assert m["D"]["under_reserve_usd"] == pytest.approx(0.0)  # 3360 >= 3200
    assert (m["D"]["true_pos"], m["D"]["false_pos"], m["D"]["false_neg"]) == (2, 1, 0)


def test_calibration_ratios_follow_from_the_counts(built):
    for row in backtest.calibration(built):
        assert row["coverage_ratio"] == pytest.approx(row["reserve_usd"] / row["realized_usd"])
        assert row["precision"] == pytest.approx(
            row["true_pos"] / (row["true_pos"] + row["false_pos"])
        )
        assert row["recall"] == pytest.approx(
            row["true_pos"] / (row["true_pos"] + row["false_neg"])
        )


def test_cohort_view_shows_reserve_building_only_after_the_write_off_is_visible(built):
    view = {r["snapshot_date"]: r for r in backtest.cohort_view(built, "P-07")}
    assert view[date(2024, 9, 30)]["reserve_d"] == 0
    assert view[date(2024, 9, 30)]["realized_usd"] > 50_000
    assert view[date(2025, 3, 31)]["realized_usd"] is None
    assert view[date(2025, 3, 31)]["scrap_usd"] > 0  # the month-30 scrap lands in that quarter


def test_policy_d_equals_policy_b_because_aged_stock_has_no_recent_usage(built):
    stale_with_usage = built.execute(
        "SELECT count(*) FROM position_asof "
        "WHERE days_since_movement > 180 AND trailing_avg_usage > 0"
    ).fetchone()[0]
    assert stale_with_usage == 0
    differing = built.execute(
        "SELECT count(*) FROM reserve_asof AS b "
        "JOIN reserve_asof AS d USING (snapshot_date, sku_id) "
        "WHERE b.policy = 'B' AND d.policy = 'D' AND abs(b.reserve_usd - d.reserve_usd) > 1e-9"
    ).fetchone()[0]
    assert differing == 0
