from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest

from eobacktest import backtest, dax
from eobacktest.pbip_validate import (
    page_filters,
    parse_table,
    policy_slicer_default,
    quarter_slicer_default,
    validate_project,
)
from eobacktest.pbip_writer import project_files
from eobacktest.registry import MEASURE_NAMES, MEASURES
from eobacktest.schema import TABLES

ROOT = Path(__file__).resolve().parents[1]
POWERBI = ROOT / "powerbi"
MODEL = "EOBacktest.SemanticModel/definition"

REGISTRY = {
    "Reserve $",
    "Realized E&O $",
    "Coverage Ratio",
    "Under-reserve $",
    "Flag Precision",
    "Flag Recall",
    "Months of Supply (Forecast)",
    "Months of Supply (Trailing)",
}


@pytest.fixture()
def project(tmp_path: Path, star_dir: Path) -> tuple[Path, Path]:
    """A scratch copy of the checked-in project next to freshly exported CSVs."""
    root = tmp_path / "powerbi"
    shutil.copytree(POWERBI, root, ignore=shutil.ignore_patterns("data"))
    data = tmp_path / "data"
    shutil.copytree(star_dir, data)
    return root / "EOBacktest.pbip", data


def _rows(path: Path) -> list[list[str]]:
    with path.open(newline="") as fh:
        return list(csv.reader(fh))


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def test_checked_in_project_validates_against_checked_in_csvs():
    assert validate_project(POWERBI / "EOBacktest.pbip", POWERBI / "data") == []


def test_project_validates_against_freshly_exported_csvs(project):
    pbip, data = project
    assert validate_project(pbip, data) == []


def test_registry_is_exactly_the_eight_required_measures():
    assert set(MEASURE_NAMES) == REGISTRY
    assert len(MEASURES) == 8


def test_model_defines_the_registry_measures_with_exact_names():
    defined = set()
    for path in (POWERBI / MODEL / "tables").glob("*.tmdl"):
        defined |= set(parse_table(path.read_text()).measures)
    assert defined == REGISTRY


def test_a_measure_referencing_a_missing_column_fails_naming_measure_and_column(project):
    pbip, data = project
    _edit(
        pbip.parent / MODEL / "tables" / "fct_reserve.tmdl",
        "SUM(fct_reserve[reserve_usd])",
        "SUM(fct_reserve[reserve_dollars])",
    )
    errors = validate_project(pbip, data)
    assert any("'Reserve $'" in e and "fct_reserve[reserve_dollars]" in e for e in errors)


def test_a_missing_or_extra_measure_breaks_registry_equality(project):
    pbip, data = project
    _edit(
        pbip.parent / MODEL / "tables" / "fct_backtest.tmdl",
        "measure 'Flag Recall'",
        "measure 'Recall'",
    )
    errors = validate_project(pbip, data)
    assert any("missing ['Flag Recall']" in e and "unexpected ['Recall']" in e for e in errors)


def test_a_measure_referencing_an_unknown_measure_fails(project):
    pbip, data = project
    path = pbip.parent / MODEL / "tables" / "fct_backtest.tmdl"
    _edit(
        path,
        "measure 'Under-reserve $' = SUM(fct_backtest[under_reserve_usd])",
        "measure 'Under-reserve $' = [Reserve Dollars] - 1",
    )
    errors = validate_project(pbip, data)
    assert any("'Under-reserve $'" in e and "[Reserve Dollars]" in e for e in errors)


def test_a_column_missing_from_the_csv_header_fails(project):
    pbip, data = project
    path = data / "dim_policy.csv"
    path.write_text(path.read_text().replace("policy_name", "policy_label", 1))
    errors = validate_project(pbip, data)
    assert any("dim_policy" in e and "policy_name" in e and "header" in e for e in errors)


def test_a_duplicate_key_on_the_one_side_of_a_relationship_fails(project):
    pbip, data = project
    path = data / "dim_policy.csv"
    lines = path.read_text().splitlines()
    lines[2] = "1" + lines[2][lines[2].index(",") :]
    path.write_text("\n".join(lines) + "\n")
    errors = validate_project(pbip, data)
    assert any("dim_policy.policy_key is not unique" in e for e in errors)


def test_a_fact_row_without_a_dimension_match_fails(project):
    pbip, data = project
    path = data / "fct_backtest.csv"
    rows = _rows(path)
    rows[1][0] = "99"
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    errors = validate_project(pbip, data)
    assert any("fct_backtest.policy_key" in e and "no match" in e for e in errors)


def test_a_visual_bound_to_a_missing_field_fails(project):
    pbip, data = project
    _edit(pbip.parent / "EOBacktest.Report" / "report.json", "Coverage Ratio", "Coverage Percent")
    errors = validate_project(pbip, data)
    assert any("Coverage Percent" in e and "visual calibration_coverage" in e for e in errors)


def test_every_page_has_a_policy_slicer_and_pages_match_the_brief():
    report = json.loads((POWERBI / "EOBacktest.Report" / "report.json").read_text())
    assert [s["displayName"] for s in report["sections"]] == [
        "Reserve Trend",
        "Backtest Calibration",
        "SKU Drill",
    ]
    for section in report["sections"]:
        slicers = [
            json.loads(v["config"])["singleVisual"]
            for v in section["visualContainers"]
            if json.loads(v["config"])["singleVisual"]["visualType"] == "slicer"
        ]
        assert any(s["prototypeQuery"]["From"][0]["Entity"] == "dim_policy" for s in slicers)


def test_data_folder_is_a_parameter_and_every_table_reads_it():
    expressions = (POWERBI / MODEL / "expressions.tmdl").read_text()
    assert "IsParameterQuery = true" in expressions
    for table in TABLES:
        text = (POWERBI / MODEL / "tables" / f"{table.name}.tmdl").read_text()
        assert f'DataFolder & "\\{table.name}.csv"' in text


def test_checked_in_project_matches_the_writer_output():
    for rel, expected in project_files().items():
        assert (POWERBI / rel).read_text() == expected, rel


def test_exported_csv_headers_and_counts_match_the_schema(star_dir, built):
    counts = {}
    for table in TABLES:
        rows = _rows(star_dir / f"{table.name}.csv")
        assert rows[0] == table.column_names
        counts[table.name] = len(rows) - 1
        assert counts[table.name] > 0
    assert counts["dim_sku"] == 400
    assert counts["dim_policy"] == 4
    assert counts["dim_date"] == 36
    assert counts["fct_backtest"] == 4 * 7
    assert counts["fct_reserve"] == 4 * counts["fct_realized"]


def test_star_keys_are_unique_on_the_dimension_side(star_dir):
    for table in (t for t in TABLES if t.key):
        with (star_dir / f"{table.name}.csv").open(newline="") as fh:
            keys = [row[table.key] for row in csv.DictReader(fh)]
        assert len(keys) == len(set(keys))


def test_checked_in_csvs_agree_with_a_fresh_seed_7_export(star_dir):
    for table in TABLES:
        fresh = _rows(star_dir / f"{table.name}.csv")
        kept = _rows(POWERBI / "data" / f"{table.name}.csv")
        assert fresh[0] == kept[0]
        assert len(fresh) == len(kept)
        for a, b in zip(fresh[1:], kept[1:], strict=True):
            for x, y in zip(a, b, strict=True):
                try:
                    assert float(x) == pytest.approx(float(y), rel=1e-9, abs=1e-6)
                except ValueError:
                    assert x == y


@pytest.mark.parametrize("measure", MEASURES, ids=lambda m: m.name)
def test_every_measure_equals_its_sql_reference_over_the_exported_csvs(measure, built, star_dir):
    def column_sum(table: str, column: str) -> float:
        (total,) = built.execute(
            f"SELECT sum({column}) FROM read_csv('{star_dir / (table + '.csv')}')"
        ).fetchone()
        return float(total)

    expressions = {m.name: m.dax for m in MEASURES}
    value = dax.evaluate(measure.name, expressions, column_sum)
    (reference,) = built.execute(measure.reference_sql).fetchone()
    assert value == pytest.approx(reference, rel=1e-6)


def test_dax_reader_finds_columns_and_measures_and_evaluates_arithmetic():
    columns, measures = dax.references("DIVIDE(SUM(t[a]), SUM(t[b]) / 12) + [Other Measure]")
    assert columns == [("t", "a"), ("t", "b")]
    assert measures == ["Other Measure"]
    sums = {("t", "a"): 6.0, ("t", "b"): 24.0}
    value = dax.evaluate(
        "M", {"M": "DIVIDE(SUM(t[a]), SUM(t[b]) / 12) + [N]", "N": "1"}, lambda t, c: sums[(t, c)]
    )
    assert value == pytest.approx(4.0)
    zero = dax.evaluate("Z", {"Z": "DIVIDE(1, 0)"}, lambda t, c: 0.0)
    assert zero != zero  # nan: DIVIDE by zero is blank in DAX


def _section(name: str) -> dict:
    report = json.loads((POWERBI / "EOBacktest.Report" / "report.json").read_text())
    return next(sec for sec in report["sections"] if sec["displayName"] == name)


def _expressions() -> dict[str, str]:
    return {m.name: m.dax for m in MEASURES}


def test_calibration_table_reserve_realized_and_coverage_reconcile_per_policy(built, star_dir):
    """Evaluate the measures the table shows under the page's own filters."""
    page = page_filters(_section("Backtest Calibration"))
    assert page == [("dim_date", "is_scored", [1])]
    reference = {r["policy"]: r for r in backtest.calibration(built)}
    for code in "ABCD":
        sums = dax.csv_column_sum(star_dir, [*page, ("dim_policy", "policy_code", [code])])

        def value(name: str, sums=sums) -> float:
            return dax.evaluate(name, _expressions(), sums)

        ref = reference[code]
        reserve, realized = value("Reserve $"), value("Realized E&O $")
        assert reserve == pytest.approx(ref["reserve_usd"], rel=1e-6)
        assert realized == pytest.approx(ref["realized_usd"], rel=1e-6)
        assert value("Coverage Ratio") == pytest.approx(reserve / realized, rel=1e-6)
        assert value("Under-reserve $") == pytest.approx(ref["under_reserve_usd"], rel=1e-6)
        assert value("Flag Precision") == pytest.approx(ref["precision"], rel=1e-6)
        assert value("Flag Recall") == pytest.approx(ref["recall"], rel=1e-6)


def test_without_the_page_filter_the_calibration_table_would_not_reconcile(star_dir):
    sums = dax.csv_column_sum(star_dir, [("dim_policy", "policy_code", ["A"])])
    reserve = dax.evaluate("Reserve $", _expressions(), sums)
    coverage = dax.evaluate("Coverage Ratio", _expressions(), sums)
    realized = dax.evaluate("Realized E&O $", _expressions(), sums)
    assert reserve / realized != pytest.approx(coverage, rel=1e-3)  # 11 quarter-ends vs 7


def test_realized_is_policy_independent_so_every_policy_row_shows_the_same_value(star_dir):
    page = page_filters(_section("Backtest Calibration"))
    values = {
        dax.evaluate(
            "Realized E&O $",
            _expressions(),
            dax.csv_column_sum(star_dir, [*page, ("dim_policy", "policy_code", [code])]),
        )
        for code in "ABCD"
    }
    assert len(values) == 1


def test_sku_drill_defaults_to_one_quarter_end_and_stock_is_not_added_across_snapshots(
    built, star_dir
):
    default = quarter_slicer_default(_section("SKU Drill"))
    assert default == ["2024 Q4"]
    pooled = dax.evaluate(
        "Months of Supply (Forecast)", _expressions(), dax.csv_column_sum(star_dir)
    )
    one = dax.evaluate(
        "Months of Supply (Forecast)",
        _expressions(),
        dax.csv_column_sum(star_dir, [("dim_date", "quarter_label", default)]),
    )
    (expected,) = built.execute(
        "SELECT sum(on_hand_units) / (sum(forecast_12m_units) / 12) FROM position_asof "
        "WHERE snapshot_date = DATE '2024-12-31'"
    ).fetchone()
    assert one == pytest.approx(expected, rel=1e-6)
    assert one != pytest.approx(pooled, rel=1e-3)
    on_hand = dax.csv_column_sum(star_dir, [("dim_date", "quarter_label", default)])
    (snapshot_units,) = built.execute(
        "SELECT sum(on_hand_units) FROM position_asof WHERE snapshot_date = DATE '2024-12-31'"
    ).fetchone()
    assert on_hand("fct_realized", "on_hand_units") == pytest.approx(snapshot_units)


def test_reserve_trend_shows_months_of_supply_by_quarter_not_pooled_cards():
    visuals = {
        json.loads(v["config"])["name"]: json.loads(v["config"])["singleVisual"]
        for v in _section("Reserve Trend")["visualContainers"]
    }
    supply = visuals["trend_supply"]
    assert supply["visualType"] == "lineChart"
    assert supply["projections"]["Category"] == [{"queryRef": "dim_date.quarter_label"}]
    assert not [n for n, v in visuals.items() if v["visualType"] == "card"]


def _edit_report(pbip: Path, change) -> None:
    path = pbip.parent / "EOBacktest.Report" / "report.json"
    report = json.loads(path.read_text())
    change(report)
    path.write_text(json.dumps(report, indent=2) + "\n")


def test_validator_rejects_a_calibration_page_without_the_scored_filter(project):
    pbip, data = project

    def drop_filter(report):
        for sec in report["sections"]:
            if sec["displayName"] == "Backtest Calibration":
                sec["filters"] = "[]"

    _edit_report(pbip, drop_filter)
    errors = validate_project(pbip, data)
    assert any("calibration_table" in e and "add up across quarter-ends" in e for e in errors)


def test_validator_rejects_stock_measures_with_no_quarter_context(project):
    pbip, data = project

    def drop_slicer(report):
        for sec in report["sections"]:
            if sec["displayName"] == "SKU Drill":
                sec["visualContainers"] = [
                    v
                    for v in sec["visualContainers"]
                    if json.loads(v["config"])["name"] != "drill_quarter"
                ]

    _edit_report(pbip, drop_slicer)
    errors = validate_project(pbip, data)
    assert any("drill_table" in e and "add up across quarter-ends" in e for e in errors)


def test_validator_rejects_a_quarter_default_that_is_not_in_dim_date(project):
    pbip, data = project
    _edit(pbip.parent / "EOBacktest.Report" / "report.json", "'2024 Q4'", "'2031 Q9'")
    errors = validate_project(pbip, data)
    assert any("'2031 Q9'" in e and "quarter_label" in e for e in errors)


def test_validator_rejects_a_slicer_that_is_not_single_select(project):
    pbip, data = project

    def multi_select(report):
        for sec in report["sections"]:
            for v in sec["visualContainers"]:
                cfg = json.loads(v["config"])
                if cfg["name"] == "drill_quarter":
                    sel = cfg["singleVisual"]["objects"]["selection"][0]["properties"]
                    sel["singleSelect"]["expr"]["Literal"]["Value"] = "false"
                    v["config"] = json.dumps(cfg, separators=(",", ":"))

    _edit_report(pbip, multi_select)
    errors = validate_project(pbip, data)
    assert any("drill_table" in e and "add up across quarter-ends" in e for e in errors)


def test_validator_rejects_a_page_filter_on_a_missing_column(project):
    pbip, data = project

    def bad_filter(report):
        for sec in report["sections"]:
            if sec["displayName"] == "Backtest Calibration":
                sec["filters"] = sec["filters"].replace("is_scored", "is_graded")

    _edit_report(pbip, bad_filter)
    errors = validate_project(pbip, data)
    assert any("page filter on missing column dim_date[is_graded]" in e for e in errors)


def test_sku_drill_reserve_is_one_policy_at_one_quarter_end_not_summed_across_policies(
    built, star_dir
):
    section = _section("SKU Drill")
    policy, quarter = policy_slicer_default(section), quarter_slicer_default(section)
    assert policy == ["D"] and quarter == ["2024 Q4"]
    context = [("dim_date", "quarter_label", quarter), ("dim_policy", "policy_code", policy)]
    shown = dax.evaluate("Reserve $", _expressions(), dax.csv_column_sum(star_dir, context))
    (expected,) = built.execute(
        "SELECT sum(reserve_usd) FROM reserve_asof "
        "WHERE policy = 'D' AND snapshot_date = DATE '2024-12-31'"
    ).fetchone()
    assert shown == pytest.approx(expected, rel=1e-6)
    all_policies = dax.evaluate(
        "Reserve $",
        _expressions(),
        dax.csv_column_sum(star_dir, [("dim_date", "quarter_label", quarter)]),
    )
    (four,) = built.execute(
        "SELECT sum(reserve_usd) FROM reserve_asof WHERE snapshot_date = DATE '2024-12-31'"
    ).fetchone()
    assert all_policies == pytest.approx(four, rel=1e-6)
    assert all_policies > 1.5 * shown  # the double-counting the default prevents


def _mutate_drill_policy_slicer(pbip: Path, change) -> None:
    def apply(report):
        for sec in report["sections"]:
            sec["visualContainers"] = [
                v
                for v in sec["visualContainers"]
                if (cfg := json.loads(v["config"]))["name"] != "drill_policy"
                or change(v, cfg) is not None
            ]

    _edit_report(pbip, apply)


def test_validator_rejects_reserve_on_sku_drill_without_a_defaulted_policy_slicer(project):
    pbip, data = project

    def plain_slicer(v, cfg):
        del cfg["singleVisual"]["objects"]
        v["config"] = json.dumps(cfg, separators=(",", ":"))
        return v

    _mutate_drill_policy_slicer(pbip, plain_slicer)
    errors = validate_project(pbip, data)
    assert any(
        "drill_table" in e and "Reserve $" in e and "summed across policies" in e for e in errors
    )


def test_validator_rejects_a_drill_page_with_no_policy_slicer_at_all(project):
    pbip, data = project
    _mutate_drill_policy_slicer(pbip, lambda v, cfg: None)
    errors = validate_project(pbip, data)
    assert any("drill_table" in e and "summed across policies" in e for e in errors)
    assert any("has no slicer on dim_policy[policy_code]" in e for e in errors)


def test_validator_rejects_a_policy_default_that_is_not_a_policy(project):
    pbip, data = project
    _edit(pbip.parent / "EOBacktest.Report" / "report.json", "'D'", "'Z'")
    errors = validate_project(pbip, data)
    assert any("'Z'" in e and "policy_code" in e for e in errors)


def test_definition_pbir_version_matches_the_legacy_report_layout():
    pbir = json.loads((POWERBI / "EOBacktest.Report" / "definition.pbir").read_text())
    assert pbir["version"] == "1.0"
    assert (POWERBI / "EOBacktest.Report" / "report.json").exists()


def test_validator_rejects_a_pbir_version_that_does_not_match_the_layout(project):
    pbip, data = project
    path = pbip.parent / "EOBacktest.Report" / "definition.pbir"
    path.write_text(path.read_text().replace('"1.0"', '"4.0"'))
    errors = validate_project(pbip, data)
    assert any("definition.pbir version is '4.0'" in e and "'1.0'" in e for e in errors)
