"""DuckDB access: schema, CSV loading and the as-of reference queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import duckdb

from . import config as C

_LOAD_ORDER = (
    ("phone_models.csv", "phone_model"),
    ("skus.csv", "sku"),
    ("lifecycle.csv", "lifecycle"),
    ("inventory_snapshots.csv", "inventory_snapshot"),
    ("forecast_versions.csv", "forecast_version"),
    ("sales.csv", "sales"),
    ("scrap.csv", "scrap"),
)


class LookaheadError(RuntimeError):
    """Raised when a position row used a forecast version dated after its snapshot."""

    def __init__(self, rows: list[tuple[Any, ...]]):
        self.rows = rows
        first = rows[0]
        super().__init__(
            f"{len(rows)} lookahead rows; first: snapshot {first[0]}, sku {first[1]}, "
            f"forecast version dated {first[2]}"
        )


def sql(name: str) -> str:
    """Text of a bundled SQL file."""
    return resources.files("eobacktest").joinpath("sql", f"{name}.sql").read_text()


def connect(path: str | Path = ":memory:") -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path))


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    for statement in sql("schema").split(";"):
        if statement.strip():
            con.execute(statement)


def load_csvs(con: duckdb.DuckDBPyConnection, data_dir: Path) -> None:
    for filename, table in _LOAD_ORDER:
        path = data_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"{path} not found; run `eobacktest generate` first")
        con.execute(f"COPY {table} FROM '{path}' (HEADER)")


def snapshot_dates() -> list[date]:
    return [C.month_end(m) for m in C.QUARTER_END_MONTHS]


def is_scored(con: duckdb.DuckDBPyConnection, snap: date) -> bool:
    """True when all 12 months after the snapshot have sales on record."""
    last = con.execute("SELECT max(sales_month) FROM sales").fetchone()
    assert last is not None and last[0] is not None
    first_of = date(snap.year, snap.month, 1)
    months = (last[0].year - first_of.year) * 12 + last[0].month - first_of.month
    return months >= C.HORIZON_MONTHS


def compute_position(con: duckdb.DuckDBPyConnection, snap: date, text: str | None = None) -> None:
    con.execute(text or sql("position"), {"snap": snap})


def compute_reserves(con: duckdb.DuckDBPyConnection, snap: date) -> None:
    con.execute(sql("reserves"), {"snap": snap})


def compute_realized(con: duckdb.DuckDBPyConnection, snap: date) -> None:
    con.execute(sql("realized"), {"snap": snap})


def lookahead_rows(con: duckdb.DuckDBPyConnection) -> list[tuple[Any, ...]]:
    return con.execute(sql("lookahead")).fetchall()


def assert_no_lookahead(con: duckdb.DuckDBPyConnection) -> None:
    rows = lookahead_rows(con)
    if rows:
        raise LookaheadError(rows)


@dataclass
class BuildSummary:
    snapshots: int
    scored_snapshots: int
    position_rows: int
    reserve_rows: int
    realized_rows: int


def build(con: duckdb.DuckDBPyConnection, snapshots: list[date] | None = None) -> BuildSummary:
    """Replay every quarter-end: position, reserves, realized E&O; then the lookahead guard."""
    for table in ("position_asof", "reserve_asof", "realized_asof"):
        con.execute(f"DELETE FROM {table}")
    dates = snapshots or snapshot_dates()
    scored = 0
    for snap in dates:
        compute_position(con, snap)
        compute_reserves(con, snap)
        if is_scored(con, snap):
            compute_realized(con, snap)
            scored += 1
    assert_no_lookahead(con)

    def count(table: str) -> int:
        row = con.execute(f"SELECT count(*) FROM {table}").fetchone()
        assert row is not None
        return int(row[0])

    return BuildSummary(
        snapshots=len(dates),
        scored_snapshots=scored,
        position_rows=count("position_asof"),
        reserve_rows=count("reserve_asof"),
        realized_rows=count("realized_asof"),
    )


def fetch_dicts(
    con: duckdb.DuckDBPyConnection, query: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    cur = con.execute(query, params or {})
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
