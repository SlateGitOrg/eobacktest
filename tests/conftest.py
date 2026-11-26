from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from eobacktest import backtest, db
from eobacktest.export import export_star
from eobacktest.generate import generate

SEED = 7


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("data")
    generate(SEED, path)
    return path


@pytest.fixture(scope="session")
def truth(data_dir: Path) -> dict:
    return json.loads((data_dir / "truth.json").read_text())


@pytest.fixture(scope="session")
def built(data_dir: Path) -> duckdb.DuckDBPyConnection:
    con = db.connect()
    db.create_schema(con)
    db.load_csvs(con, data_dir)
    db.build(con)
    backtest.compute_backtest(con)
    return con


@pytest.fixture(scope="session")
def star_dir(built: duckdb.DuckDBPyConnection, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("star")
    export_star(built, path)
    return path
