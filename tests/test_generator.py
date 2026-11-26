from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from eobacktest import config as C
from eobacktest.generate import decline_multiplier, generate


def _digest(path: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.iterdir())}


def test_same_seed_gives_identical_files_and_another_seed_differs(tmp_path, data_dir):
    generate(7, tmp_path / "again")
    assert _digest(tmp_path / "again") == _digest(data_dir)
    generate(8, tmp_path / "other")
    assert _digest(tmp_path / "other")["sales.csv"] != _digest(data_dir)["sales.csv"]


def test_catalogue_has_400_skus_linked_to_12_phone_models(built):
    assert built.execute("SELECT count(*) FROM sku").fetchone()[0] == 400
    assert built.execute("SELECT count(DISTINCT phone_model) FROM sku").fetchone()[0] == 12
    assert built.execute("SELECT count(*) FROM phone_model").fetchone()[0] == 12


def test_p07_and_p09_are_discontinued_in_months_20_and_28_with_60_skus(built, truth):
    rows = dict(
        built.execute(
            "SELECT model_id, discontinued_month FROM phone_model "
            "WHERE discontinued_month IS NOT NULL"
        ).fetchall()
    )
    assert rows == {"P-07": C.month_start(20), "P-09": C.month_start(28)}
    cohort = truth["cohorts"]["P-07"] + truth["cohorts"]["P-09"]
    assert len(cohort) == 60
    assert (
        built.execute("SELECT count(*) FROM sku WHERE phone_model IN ('P-07', 'P-09')").fetchone()[
            0
        ]
        == 60
    )


def test_there_are_36_monthly_snapshots_of_stock(built):
    months = built.execute(
        "SELECT count(DISTINCT snapshot_date) FROM inventory_snapshot"
    ).fetchone()
    assert months[0] == 36
    first = built.execute("SELECT min(snapshot_date) FROM inventory_snapshot").fetchone()[0]
    assert first == date(2023, 1, 31)


def test_demand_for_p07_falls_85_percent_within_two_months_and_is_zero_after_six(built):
    def cohort_units(month: int) -> int:
        return built.execute(
            "SELECT sum(units_sold) FROM sales JOIN sku USING (sku_id) "
            "WHERE phone_model = 'P-07' AND sales_month = ?",
            [C.month_start(month)],
        ).fetchone()[0]

    before = sum(cohort_units(m) for m in range(17, 20)) / 3
    assert cohort_units(21) <= 0.15 * before * 1.25  # two months after the discontinuation month
    assert all(cohort_units(m) == 0 for m in range(26, 37))
    assert decline_multiplier(1) == 0.15
    assert decline_multiplier(6) == 0.0


def test_scrap_happens_only_for_discontinued_models_in_months_30_and_34(built):
    months = built.execute("SELECT DISTINCT txn_date FROM scrap ORDER BY 1").fetchall()
    assert [m[0] for m in months] == [C.month_end(30), C.month_end(34)]
    models = {
        r[0]
        for r in built.execute(
            "SELECT DISTINCT phone_model FROM scrap JOIN sku USING (sku_id)"
        ).fetchall()
    }
    assert models == {"P-07", "P-09"}


def test_dormant_skus_sell_nothing_and_hold_800_to_2000_units(built, truth):
    ids = truth["dormant_skus"]
    assert len(ids) == 15
    sold = built.execute(
        "SELECT sum(units_sold) FROM sales WHERE sku_id IN (SELECT unnest($i))", {"i": ids}
    ).fetchone()[0]
    assert sold == 0
    low, high = built.execute(
        "SELECT min(on_hand_units), max(on_hand_units) FROM inventory_snapshot "
        "WHERE sku_id IN (SELECT unnest($i))",
        {"i": ids},
    ).fetchone()
    assert 800 <= low and high <= 2000
    stages = built.execute(
        "SELECT DISTINCT stage FROM lifecycle WHERE sku_id IN (SELECT unnest($i))", {"i": ids}
    ).fetchall()
    assert stages == [("dormant",)]


def test_gift_packs_sell_only_from_october_to_december(built, truth):
    ids = truth["gift_pack_skus"]
    assert len(ids) == 25
    months = {
        r[0]
        for r in built.execute(
            "SELECT DISTINCT month(sales_month) FROM sales "
            "WHERE units_sold > 0 AND sku_id IN (SELECT unnest($i))",
            {"i": ids},
        ).fetchall()
    }
    assert months == {10, 11, 12}


def test_stock_is_never_negative_and_last_movement_never_in_the_future(built):
    bad = built.execute(
        "SELECT count(*) FROM inventory_snapshot "
        "WHERE on_hand_units < 0 OR last_movement_date > snapshot_date"
    ).fetchone()[0]
    assert bad == 0


def test_end_of_life_stock_builds_up_before_it_is_scrapped(built):
    on_hand = {
        m: built.execute(
            "SELECT sum(on_hand_units * inventory_snapshot.unit_cost) FROM inventory_snapshot "
            "JOIN sku USING (sku_id) "
            "WHERE phone_model = 'P-07' AND snapshot_date = ?",
            [C.month_end(m)],
        ).fetchone()[0]
        for m in (18, 24, 29, 35)
    }
    assert on_hand[24] > 1.4 * on_hand[18]  # purchasing kept buying to a decaying forecast
    assert on_hand[35] == 0  # scrapped
