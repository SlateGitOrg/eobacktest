"""Star-schema CSV export for the Power BI model."""

from __future__ import annotations

from pathlib import Path

import duckdb

from .schema import TABLES


def export_star(con: duckdb.DuckDBPyConnection, out_dir: Path) -> dict[str, int]:
    """Write one CSV per star-schema table into ``out_dir``; return row counts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for table in TABLES:
        path = out_dir / f"{table.name}.csv"
        con.execute(f"COPY ({table.query}) TO '{path}' (HEADER, DELIMITER ',')")
        row = con.execute(f"SELECT count(*) FROM read_csv('{path}')").fetchone()
        assert row is not None
        counts[table.name] = int(row[0])
    return counts
