"""Seeded synthetic data for a consumer-electronics accessories brand.

The generator simulates 400 SKUs for 36 months: demand, purchasing against a forecast with a
3-month lead time and minimum order quantities, monthly stock snapshots, monthly 12-month
forecast versions, and scrap. Everything is derived from one numpy ``Generator`` so a seed
always yields byte-identical files.

Injected patterns (listed in ``truth.json``):

* Phone models P-07 and P-09 are discontinued in months 20 and 28 (60 SKUs). Demand falls 85%
  within two months and reaches zero after six. Planners decay the forecast only 10% a month
  from the pre-discontinuation level, so purchasing keeps going and excess builds. Stock is
  scrapped in months 30 and 34.
* 15 dormant SKUs sell nothing for the whole window but keep a high forecast.
* 25 seasonal gift packs sell only October to December and carry a small leftover each summer.
* A forecast version dated after the last snapshot with a zero forecast for 12 P-07 SKUs: a
  join that does not respect version dates would let those SKUs look perfectly forecast.
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from . import config as C

# (model id, launch month index, discontinuation month index or None)
PHONE_MODELS: tuple[tuple[str, int, int | None], ...] = (
    ("P-01", -40, None),
    ("P-02", -34, None),
    ("P-03", -28, None),
    ("P-04", -22, None),
    ("P-05", -16, None),
    ("P-06", -12, None),
    ("P-07", -6, 20),
    ("P-08", 3, None),
    ("P-09", -2, 28),
    ("P-10", 6, None),
    ("P-11", 12, None),
    ("P-12", 20, None),
)
DISCONTINUED = {model: month for model, _, month in PHONE_MODELS if month is not None}
SCRAP_MONTHS = (30, 34)

GIFT_PROFILE = {10: 0.25, 11: 0.40, 12: 0.35}
RAMP = (0.25, 0.5, 0.8)  # demand multiplier in the first three months after launch
DECAY = 0.9  # monthly decay of the planner forecast after discontinuation
LEAD_MONTHS = 3
N_PLANTED = 12
PLANTED_VERSION_DATE = date(2026, 2, 15)

_GIFT_PER_MODEL = {"P-01": 4, "P-02": 4, "P-03": 4, "P-04": 4, "P-05": 4, "P-06": 5}
_DORMANT_PER_MODEL = {"P-01": 5, "P-02": 5, "P-03": 5}
_CATEGORIES = (
    ("case", 3.0, 7.0, 0.30),
    ("screen protector", 1.5, 3.0, 0.20),
    ("charger", 8.0, 16.0, 0.20),
    ("cable", 2.5, 6.0, 0.20),
    ("car mount", 6.0, 12.0, 0.10),
)


@dataclass
class Sku:
    sku_id: str
    name: str
    model: str
    category: str
    kind: str  # standard | gift_pack
    role: str  # standard | gift | dormant
    unit_cost: float
    base: float  # units per month at maturity (standard) or 0
    annual: float  # units per season (gift packs) or 0
    moq: int
    l0: int  # launch month index; <= 0 means already on sale before the data starts
    dead_stock: int = 0
    dead_forecast: float = 0.0


def _lognorm(rng: np.random.Generator, sigma: float) -> float:
    """Mean-one lognormal noise, so forecasts built from it are unbiased."""
    return float(rng.lognormal(-0.5 * sigma * sigma, sigma))


def _moq_ceil(qty: float, moq: int) -> int:
    return int(math.ceil(max(qty, 1e-9) / moq) * moq)


def decline_multiplier(months_after: int) -> float:
    """Demand multiplier k months after the discontinuation month (k = 0 is that month).

    Down 85% by the second month, to zero once six months have passed.
    """
    if months_after < 0:
        return 1.0
    return {0: 0.4, 1: 0.15, 2: 0.10, 3: 0.07, 4: 0.04, 5: 0.02}.get(months_after, 0.0)


def _build_skus(rng: np.random.Generator) -> list[Sku]:
    skus: list[Sku] = []
    cat_names = [c[0] for c in _CATEGORIES]
    cat_p = [c[3] for c in _CATEGORIES]
    cost_range = {c[0]: (c[1], c[2]) for c in _CATEGORIES}
    n = 0
    for model, launch, _disc in PHONE_MODELS:
        total = 30 if model in DISCONTINUED else 34
        n_gift = _GIFT_PER_MODEL.get(model, 0)
        n_dormant = _DORMANT_PER_MODEL.get(model, 0)
        for i in range(total):
            n += 1
            l0 = launch if launch <= 0 else launch + int(rng.integers(0, 3))
            if i < n_gift:
                annual = float(rng.uniform(800, 3000))
                moq = max(50, int(round(annual * 0.05, -1)))
                skus.append(
                    Sku(
                        f"SKU-{n:04d}",
                        f"Gift pack for {model} ({i + 1:02d})",
                        model,
                        "gift pack",
                        "gift_pack",
                        "gift",
                        round(float(rng.uniform(12, 28)), 2),
                        0.0,
                        annual,
                        moq,
                        l0,
                    )
                )
                continue
            category = str(rng.choice(cat_names, p=cat_p))
            lo, hi = cost_range[category]
            cost = round(float(rng.uniform(lo, hi)), 2)
            base = float(np.clip(rng.lognormal(math.log(110), 0.6), 25, 700))
            moq = max(50, int(round(base * rng.uniform(0.8, 1.6), -1)))
            role = "dormant" if i < n_gift + n_dormant else "standard"
            sku = Sku(
                f"SKU-{n:04d}",
                f"{category.title()} for {model} ({i + 1:02d})",
                model,
                category,
                "standard",
                role,
                cost,
                base,
                0.0,
                moq,
                l0,
            )
            if role == "dormant":
                sku.dead_stock = int(round(rng.uniform(800, 2000), -1))
                # Planners still expect the stock to clear within about a year.
                sku.dead_forecast = sku.dead_stock / 12 * float(rng.uniform(0.85, 1.0))
            skus.append(sku)
    return skus


@dataclass
class SimResult:
    snapshots: list[tuple[int, int, date]]  # month, on_hand, last_movement
    sales: list[tuple[int, int]]  # month, units
    forecasts: dict[int, list[float]]  # version month -> 12 forecast values
    scrap: list[tuple[int, int]]  # month, units


def _simulate(rng: np.random.Generator, s: Sku, disc: int | None) -> SimResult:
    pipeline: dict[int, int] = defaultdict(int)
    sales: dict[int, int] = {}
    snaps: list[tuple[int, int, date]] = []
    forecasts: dict[int, list[float]] = {}
    scrap: list[tuple[int, int]] = []
    on_hand = 0
    last_move: date | None = None

    if s.role == "dormant":
        on_hand = s.dead_stock
        last_move = C.month_end(-6 - int(rng.integers(0, 9)))
    elif s.l0 <= 0:
        if s.role == "gift":
            on_hand = int(round(s.annual * rng.uniform(0.04, 0.12)))
            last_move = date(C.START_YEAR - 1, 12, 31)
        else:
            on_hand = int(round(s.base * rng.uniform(1.5, 3.5)))
            last_move = C.month_end(0)
            for k in (1, 2, 3):
                pipeline[k] = _moq_ceil(s.base * rng.uniform(0.8, 1.2), s.moq)
    else:
        pipeline[s.l0] = _moq_ceil(s.base * 3, s.moq)

    level_at_disc = 0.0
    for m in range(max(1, s.l0), C.N_MONTHS + 1):
        arrived = pipeline.pop(m, 0)
        on_hand += arrived
        if arrived > 0:
            last_move = C.month_end(m)

        if s.role == "dormant":
            demand = 0.0
        elif s.role == "gift":
            share = GIFT_PROFILE.get(C.month_end(m).month, 0.0)
            demand = s.annual * share * _lognorm(rng, 0.08)
        else:
            age = m - s.l0
            ramp = RAMP[age] if age < len(RAMP) else 1.0
            fall = decline_multiplier(m - disc) if disc is not None else 1.0
            demand = s.base * ramp * fall * _lognorm(rng, 0.15)
        sold = int(min(round(demand), on_hand))
        sales[m] = sold
        on_hand -= sold
        if sold > 0:
            last_move = C.month_end(m)

        if disc is not None and m in SCRAP_MONTHS and on_hand > 0:
            qty = on_hand // 2 if m == SCRAP_MONTHS[0] else on_hand
            if qty > 0:
                on_hand -= qty
                last_move = C.month_end(m)
                scrap.append((m, qty))

        assert last_move is not None
        snaps.append((m, on_hand, last_move))

        if disc is not None and m == disc - 1:
            level_at_disc = _level(rng, s, m, sales)
        fc = _forecast(rng, s, disc, m, sales, level_at_disc)
        forecasts[m] = fc

        stopped = disc is not None and m >= SCRAP_MONTHS[0]
        if s.role != "dormant" and not stopped:
            horizon, safety = (5, 1.15)
            target = safety * sum(fc[:horizon])
            position = on_hand + sum(q for k, q in pipeline.items() if k > m)
            if position < 0.85 * target:
                pipeline[m + LEAD_MONTHS] += _moq_ceil(target - position, s.moq)

    return SimResult(
        snapshots=snaps,
        sales=sorted(sales.items()),
        forecasts=forecasts,
        scrap=scrap,
    )


def _level(rng: np.random.Generator, s: Sku, m: int, sales: dict[int, int]) -> float:
    """Planner demand level at version month m: trailing 3-month mean once three mature
    months exist, otherwise the launch plan."""
    if m - 2 >= max(1, s.l0 + 3):
        return sum(sales[k] for k in (m - 2, m - 1, m)) / 3
    return s.base * _lognorm(rng, 0.10)


def _forecast(
    rng: np.random.Generator,
    s: Sku,
    disc: int | None,
    m: int,
    sales: dict[int, int],
    level_at_disc: float,
) -> list[float]:
    horizon = range(1, C.HORIZON_MONTHS + 1)
    if s.role == "dormant":
        values = [s.dead_forecast * _lognorm(rng, 0.03) for _ in horizon]
    elif s.role == "gift":
        values = [
            s.annual * GIFT_PROFILE.get(C.month_start(m + h).month, 0.0) * _lognorm(rng, 0.08)
            for h in horizon
        ]
    elif disc is not None and m >= disc:
        values = [level_at_disc * DECAY ** (m + h - disc) * _lognorm(rng, 0.05) for h in horizon]
    else:
        level = _level(rng, s, m, sales)
        values = [level * _lognorm(rng, 0.05) for _ in horizon]
    return [round(v, 2) for v in values]


def _write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def generate(seed: int, out_dir: Path) -> dict[str, int]:
    """Write the synthetic dataset into ``out_dir`` and return row counts per file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    skus = _build_skus(rng)
    model_disc = {m: d for m, _, d in PHONE_MODELS}

    snap_rows: list[list[object]] = []
    sales_rows: list[list[object]] = []
    fc_rows: list[list[object]] = []
    scrap_rows: list[list[object]] = []
    life_rows: list[list[object]] = []
    sim_cache: dict[str, SimResult] = {}

    for s in skus:
        disc = model_disc[s.model]
        sim = _simulate(rng, s, disc)
        sim_cache[s.sku_id] = sim
        for m, on_hand, last_move in sim.snapshots:
            snap_rows.append(
                [C.month_end(m).isoformat(), s.sku_id, on_hand, s.unit_cost, last_move.isoformat()]
            )
        for m, units in sim.sales:
            sales_rows.append([C.month_start(m).isoformat(), s.sku_id, units])
        for m, values in sim.forecasts.items():
            version = C.month_end(m).isoformat()
            for h, units in enumerate(values, start=1):
                fc_rows.append([version, s.sku_id, C.month_start(m + h).isoformat(), units])
        for m, qty in sim.scrap:
            scrap_rows.append([C.month_end(m).isoformat(), s.sku_id, qty, s.unit_cost])
        life_rows.extend(_lifecycle_rows(s, disc))

    cohort_p07 = [s.sku_id for s in skus if s.model == "P-07"]
    planted = cohort_p07[:N_PLANTED]
    for sku_id in planted:
        for h in range(1, C.HORIZON_MONTHS + 1):
            target = date(
                PLANTED_VERSION_DATE.year + (PLANTED_VERSION_DATE.month - 1 + h) // 12,
                (PLANTED_VERSION_DATE.month - 1 + h) % 12 + 1,
                1,
            )
            fc_rows.append([PLANTED_VERSION_DATE.isoformat(), sku_id, target.isoformat(), 0.0])

    _write_csv(
        out_dir / "phone_models.csv",
        ["model_id", "launch_month", "discontinued_month"],
        [
            [
                model,
                C.month_start(launch).isoformat(),
                C.month_start(disc).isoformat() if disc is not None else "",
            ]
            for model, launch, disc in PHONE_MODELS
        ],
    )
    _write_csv(
        out_dir / "skus.csv",
        ["sku_id", "name", "phone_model", "category", "kind", "unit_cost"],
        [[s.sku_id, s.name, s.model, s.category, s.kind, s.unit_cost] for s in skus],
    )
    _write_csv(out_dir / "lifecycle.csv", ["sku_id", "from_month", "stage"], life_rows)
    _write_csv(
        out_dir / "inventory_snapshots.csv",
        ["snapshot_date", "sku_id", "on_hand_units", "unit_cost", "last_movement_date"],
        snap_rows,
    )
    _write_csv(
        out_dir / "forecast_versions.csv",
        ["version_date", "sku_id", "target_month", "forecast_units"],
        fc_rows,
    )
    _write_csv(out_dir / "sales.csv", ["sales_month", "sku_id", "units_sold"], sales_rows)
    _write_csv(out_dir / "scrap.csv", ["txn_date", "sku_id", "units", "unit_cost"], scrap_rows)

    truth = {
        "seed": seed,
        "months": C.N_MONTHS,
        "first_month": C.month_start(1).isoformat(),
        "discontinued": {
            model: C.month_end(month).isoformat() for model, month in DISCONTINUED.items()
        },
        "discontinued_month_index": DISCONTINUED,
        "scrap_month_index": list(SCRAP_MONTHS),
        "cohorts": {
            "P-07": cohort_p07,
            "P-09": [s.sku_id for s in skus if s.model == "P-09"],
        },
        "dormant_skus": [s.sku_id for s in skus if s.role == "dormant"],
        "gift_pack_skus": [s.sku_id for s in skus if s.role == "gift"],
        "planted_future_version": {
            "version_date": PLANTED_VERSION_DATE.isoformat(),
            "skus": planted,
            "forecast": 0.0,
        },
    }
    (out_dir / "truth.json").write_text(json.dumps(truth, indent=2) + "\n")

    return {
        "skus": len(skus),
        "snapshots": len(snap_rows),
        "forecast_rows": len(fc_rows),
        "sales": len(sales_rows),
        "scrap": len(scrap_rows),
    }


def _lifecycle_rows(s: Sku, disc: int | None) -> list[list[object]]:
    """Product-lifecycle master: the stage a SKU is in from a given month on."""
    if s.role == "dormant":
        return [[s.sku_id, C.month_start(min(s.l0, 1)).isoformat(), "dormant"]]
    rows: list[list[object]] = []
    if s.l0 >= 1:
        rows.append([s.sku_id, C.month_start(s.l0).isoformat(), "ramp"])
        rows.append([s.sku_id, C.month_start(s.l0 + len(RAMP)).isoformat(), "mature"])
    else:
        rows.append([s.sku_id, C.month_start(s.l0).isoformat(), "mature"])
    if disc is not None:
        rows.append([s.sku_id, C.month_start(disc).isoformat(), "decline"])
    return rows
