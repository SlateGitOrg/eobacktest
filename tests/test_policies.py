from __future__ import annotations

import pytest
from helpers import SNAP, add_next_year_sales, fixture_connection, reserves_at

from eobacktest import config as C
from eobacktest import db
from eobacktest.policies import aging_fraction, realized_units, reserve_policies


@pytest.fixture()
def fixture_reserves():
    con = fixture_connection()
    db.compute_position(con, SNAP)
    db.compute_reserves(con, SNAP)
    return con, reserves_at(con)


def test_policy_fixture_sku1_forecast_trailing_aging_and_max(fixture_reserves):
    _, r = fixture_reserves
    assert r[("SKU1", "A")] == pytest.approx(2400.0)
    assert r[("SKU1", "B")] == pytest.approx(3360.0)
    assert r[("SKU1", "C")] == pytest.approx(0.0)
    assert r[("SKU1", "D")] == pytest.approx(3360.0)


def test_policy_fixture_sku2_no_forecast_no_usage_is_fully_reserved(fixture_reserves):
    _, r = fixture_reserves
    for policy in C.POLICIES:
        assert r[("SKU2", policy)] == pytest.approx(5000.0)


def test_policy_fixture_sku3_only_aging_reserves(fixture_reserves):
    _, r = fixture_reserves
    assert r[("SKU3", "A")] == pytest.approx(0.0)
    assert r[("SKU3", "B")] == pytest.approx(0.0)
    assert r[("SKU3", "C")] == pytest.approx(300.0)
    assert r[("SKU3", "D")] == pytest.approx(300.0)


def test_python_reference_matches_the_policy_fixture():
    sku1 = reserve_policies(1200, 4.0, 600, 30, 20)
    assert (sku1.a, sku1.b, sku1.c, sku1.d) == pytest.approx((2400, 3360, 0, 3360))
    sku2 = reserve_policies(500, 10.0, 0, 0, 400)
    assert sku2.as_dict() == pytest.approx({"A": 5000, "B": 5000, "C": 5000, "D": 5000})
    sku3 = reserve_policies(300, 2.0, 400, 50, 200)
    assert sku3.as_dict() == pytest.approx({"A": 0, "B": 0, "C": 300, "D": 300})


@pytest.mark.parametrize(
    ("days", "fraction"), [(0, 0.0), (180, 0.0), (181, 0.5), (365, 0.5), (366, 1.0), (900, 1.0)]
)
def test_aging_thresholds_are_strictly_greater_than(days, fraction):
    assert aging_fraction(days) == fraction


def test_sql_and_python_reserves_agree_on_every_generated_row(built):
    rows = built.execute(
        """
        SELECT p.on_hand_units, p.unit_cost, p.forecast_12m_units, p.trailing_avg_usage,
               p.days_since_movement, r.policy, r.reserve_usd
        FROM position_asof AS p JOIN reserve_asof AS r USING (snapshot_date, sku_id)
        """
    ).fetchall()
    assert len(rows) == 4101 * 4
    for on_hand, cost, fc, usage, days, policy, usd in rows:
        expected = reserve_policies(on_hand, cost, fc, usage, days).as_dict()[policy]
        assert usd == pytest.approx(expected, rel=1e-9, abs=1e-9)


def test_reserves_are_non_negative_capped_at_cost_and_d_is_the_max_of_b_and_c(built):
    rows = built.execute(
        """
        SELECT p.on_hand_units * p.unit_cost AS value,
               max(r.reserve_usd) FILTER (WHERE r.policy = 'A') AS a,
               max(r.reserve_usd) FILTER (WHERE r.policy = 'B') AS b,
               max(r.reserve_usd) FILTER (WHERE r.policy = 'C') AS c,
               max(r.reserve_usd) FILTER (WHERE r.policy = 'D') AS d
        FROM position_asof AS p JOIN reserve_asof AS r USING (snapshot_date, sku_id)
        GROUP BY p.snapshot_date, p.sku_id, p.on_hand_units, p.unit_cost
        """
    ).fetchall()
    for value, a, b, c, d in rows:
        assert min(a, b, c, d) >= 0
        assert max(a, b, c, d) <= value + 1e-6
        assert d == pytest.approx(max(b, c))


def test_realized_fixture_counts_unsold_units():
    con = fixture_connection()
    add_next_year_sales(con, {"SKU1": 650})
    con.execute("DELETE FROM inventory_snapshot WHERE sku_id <> 'SKU1'")
    con.execute("UPDATE inventory_snapshot SET on_hand_units = 1000")
    db.compute_realized(con, SNAP)
    units, usd = con.execute("SELECT realized_units, realized_usd FROM realized_asof").fetchone()
    assert units == 350
    assert usd == pytest.approx(350 * 4.0)
    assert realized_units(1000, 650) == 350


def test_realized_is_zero_when_demand_exceeds_stock_and_full_when_nothing_sells():
    con = fixture_connection()
    add_next_year_sales(con, {"SKU1": 5000, "SKU3": 100})
    db.compute_realized(con, SNAP)
    rows = dict(con.execute("SELECT sku_id, realized_units FROM realized_asof").fetchall())
    assert rows == {"SKU1": 0, "SKU2": 500, "SKU3": 200}


def test_position_ignores_sales_after_the_snapshot_month():
    con = fixture_connection()
    db.compute_position(con, SNAP)
    base = con.execute("SELECT sku_id, trailing_avg_usage FROM position_asof ORDER BY 1").fetchall()
    con.execute("DELETE FROM position_asof")
    add_next_year_sales(con, {"SKU1": 12000, "SKU2": 12000, "SKU3": 12000})
    db.compute_position(con, SNAP)
    after = con.execute(
        "SELECT sku_id, trailing_avg_usage FROM position_asof ORDER BY 1"
    ).fetchall()
    assert after == base
    assert dict(after)["SKU1"] == pytest.approx(30.0)
