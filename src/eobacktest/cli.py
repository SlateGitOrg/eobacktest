"""Command-line interface: generate, build, backtest, export, validate-pbip, report."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import duckdb
import typer

from . import backtest as bt
from . import db
from .export import export_star
from .generate import generate as generate_data
from .pbip_validate import validate_project
from .report import bias_table, calibration_table, render_report

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)

DEFAULT_DATA = Path("data")
DEFAULT_DB = Path("out/eobacktest.duckdb")
DEFAULT_PROJECT = Path("powerbi/EOBacktest.pbip")
DEFAULT_STAR = Path("powerbi/data")


def _open(db_path: Path) -> duckdb.DuckDBPyConnection:
    if not db_path.exists():
        typer.echo(f"{db_path} not found; run `eobacktest build` first", err=True)
        raise typer.Exit(1)
    return db.connect(db_path)


@app.command()
def generate(
    seed: Annotated[
        int, typer.Option(help="Random seed; the same seed gives identical files.")
    ] = 7,
    out: Annotated[
        Path, typer.Option(help="Directory for the generated CSV files.")
    ] = DEFAULT_DATA,
) -> None:
    """Write the synthetic dataset (SKUs, snapshots, forecasts, sales, scrap, truth.json)."""
    counts = generate_data(seed, out)
    typer.echo(f"wrote {out}/ with " + ", ".join(f"{v:,} {k}" for k, v in counts.items()))


@app.command()
def build(
    data: Annotated[Path, typer.Option(help="Directory written by `generate`.")] = DEFAULT_DATA,
    db_path: Annotated[Path, typer.Option("--db", help="DuckDB file to create.")] = DEFAULT_DB,
) -> None:
    """Load the data, replay every quarter-end point-in-time and run the lookahead guard."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = db.connect(db_path)
    db.create_schema(con)
    db.load_csvs(con, data)
    try:
        summary = db.build(con)
    except db.LookaheadError as exc:
        typer.echo(f"lookahead guard failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    bt.compute_backtest(con)
    typer.echo(
        f"replayed {summary.snapshots} quarter-ends ({summary.scored_snapshots} scored): "
        f"{summary.position_rows:,} positions, {summary.reserve_rows:,} reserve rows, "
        f"{summary.realized_rows:,} realized rows; lookahead guard returned 0 rows"
    )


@app.command()
def backtest(
    db_path: Annotated[
        Path, typer.Option("--db", help="DuckDB file made by `build`.")
    ] = DEFAULT_DB,
) -> None:
    """Print calibration per policy and forecast bias by lifecycle stage."""
    con = _open(db_path)
    bt.compute_backtest(con)
    typer.echo("Calibration by policy, all scored quarter-ends\n")
    typer.echo(calibration_table(con))
    typer.echo("\nForecast bias by lifecycle stage\n")
    typer.echo(bias_table(con))


@app.command()
def export(
    db_path: Annotated[
        Path, typer.Option("--db", help="DuckDB file made by `build`.")
    ] = DEFAULT_DB,
    out: Annotated[Path, typer.Option(help="Folder for the star-schema CSV files.")] = DEFAULT_STAR,
) -> None:
    """Export dim_sku, dim_date, dim_policy, fct_reserve, fct_realized, fct_backtest as CSV."""
    con = _open(db_path)
    bt.compute_backtest(con)
    counts = export_star(con, out)
    typer.echo(f"wrote {out}/: " + ", ".join(f"{k} {v:,} rows" for k, v in counts.items()))


@app.command("validate-pbip")
def validate_pbip(
    project: Annotated[Path, typer.Option(help="Path to the .pbip file.")] = DEFAULT_PROJECT,
    data: Annotated[
        Path, typer.Option(help="Folder holding the exported CSV files.")
    ] = DEFAULT_STAR,
) -> None:
    """Statically validate the Power BI project against the exported CSV headers."""
    errors = validate_project(project, data)
    if errors:
        for line in errors:
            typer.echo(f"FAIL {line}", err=True)
        raise typer.Exit(1)
    typer.echo(f"{project}: model, relationships and report bindings are valid")


@app.command()
def report(
    db_path: Annotated[
        Path, typer.Option("--db", help="DuckDB file made by `build`.")
    ] = DEFAULT_DB,
    out: Annotated[
        Path | None, typer.Option(help="Write Markdown here instead of printing it.")
    ] = None,
) -> None:
    """Markdown backtest table per policy and the end-of-life cohort view per quarter-end."""
    text = render_report(_open(db_path))
    if out is None:
        typer.echo(text)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        typer.echo(f"wrote {out}")


if __name__ == "__main__":
    app()
