from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from eobacktest.cli import app

runner = CliRunner()


@pytest.fixture(scope="module")
def workdir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("cli")


def test_full_pipeline_from_generate_to_validate(workdir: Path):
    data, db_file, star = workdir / "data", workdir / "out" / "eo.duckdb", workdir / "star"

    r = runner.invoke(app, ["generate", "--seed", "7", "--out", str(data)])
    assert r.exit_code == 0, r.output
    assert "400 skus" in r.output

    r = runner.invoke(app, ["build", "--data", str(data), "--db", str(db_file)])
    assert r.exit_code == 0, r.output
    assert "11 quarter-ends (7 scored)" in r.output
    assert "lookahead guard returned 0 rows" in r.output

    r = runner.invoke(app, ["backtest", "--db", str(db_file)])
    assert r.exit_code == 0, r.output
    assert "| A |" in r.output and "| D |" in r.output
    assert "mature" in r.output and "decline" in r.output

    r = runner.invoke(app, ["export", "--db", str(db_file), "--out", str(star)])
    assert r.exit_code == 0, r.output
    assert (star / "fct_reserve.csv").exists()

    project = Path(__file__).resolve().parents[1] / "powerbi" / "EOBacktest.pbip"
    r = runner.invoke(app, ["validate-pbip", "--project", str(project), "--data", str(star)])
    assert r.exit_code == 0, r.output
    assert "valid" in r.output

    out = workdir / "report.md"
    r = runner.invoke(app, ["report", "--db", str(db_file), "--out", str(out)])
    assert r.exit_code == 0, r.output
    text = out.read_text()
    assert "## End-of-life cohort P-07 (discontinued August 2024)" in text
    assert "## End-of-life cohort P-09 (discontinued April 2025)" in text
    assert "### Policy D: Max of B and C" in text


def test_validate_pbip_exits_non_zero_and_names_the_problem(tmp_path: Path):
    project = Path(__file__).resolve().parents[1] / "powerbi" / "EOBacktest.pbip"
    r = runner.invoke(app, ["validate-pbip", "--project", str(project), "--data", str(tmp_path)])
    assert r.exit_code == 1
    assert "FAIL" in r.output and "not found" in r.output


def test_build_fails_when_the_data_is_missing(tmp_path: Path):
    r = runner.invoke(app, ["build", "--data", str(tmp_path), "--db", str(tmp_path / "x.duckdb")])
    assert r.exit_code != 0


def test_commands_that_need_a_database_say_so(tmp_path: Path):
    r = runner.invoke(app, ["backtest", "--db", str(tmp_path / "missing.duckdb")])
    assert r.exit_code == 1
    assert "run `eobacktest build` first" in r.output
