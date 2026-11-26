"""Plain-Python reference for the four reserve policies.

The SQL in ``sql/reserves.sql`` is the system of record; this module restates the same
rules so tests can compare the two on every generated row.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config as C


@dataclass(frozen=True)
class Reserves:
    a: float
    b: float
    c: float
    d: float

    def as_dict(self) -> dict[str, float]:
        return {"A": self.a, "B": self.b, "C": self.c, "D": self.d}


def aging_fraction(days_since_movement: int) -> float:
    if days_since_movement > C.AGING_FULL_DAYS:
        return 1.0
    if days_since_movement > C.AGING_HALF_DAYS:
        return 0.5
    return 0.0


def reserve_policies(
    on_hand: float,
    unit_cost: float,
    forecast_12m_units: float,
    trailing_avg_usage: float,
    days_since_movement: int,
) -> Reserves:
    """Reserve dollars for one SKU at one snapshot under policies A to D."""
    a = max(0.0, on_hand - forecast_12m_units) * unit_cost
    b = max(0.0, on_hand - 12 * trailing_avg_usage) * unit_cost
    c = on_hand * unit_cost * aging_fraction(days_since_movement)
    return Reserves(a=a, b=b, c=c, d=max(b, c))


def realized_units(on_hand: float, units_sold_next_12m: float) -> float:
    """Units of E&O that actually went unsold; sell-through consumes snapshot stock first."""
    return max(0.0, on_hand - units_sold_next_12m)
