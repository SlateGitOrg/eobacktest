"""Writes the Power BI project (PBIP: TMDL semantic model + PBIR-legacy report).

Run ``python -m eobacktest.pbip_writer powerbi`` to regenerate the checked-in files; a test
compares them with this output so the model, the export schema and the registry stay in step.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

from .registry import MEASURES
from .schema import DTYPE_M, RELATIONSHIPS, TABLES, Table

PROJECT = "EOBacktest"
# definition.pbir version: 1.0 pairs with the legacy single-file report.json layout used here
# (4.0 is for the report/definition/ folder layout).
PBIR_LEGACY_VERSION = "1.0"
DATA_FOLDER_PLACEHOLDER = "C:\\eobacktest\\powerbi\\data"
_NS = uuid.UUID("5b0d1a0e-47c1-4c3a-9d6e-2f0b8d7a11aa")


def _tag(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "/".join(parts)))


def _q(name: str) -> str:
    """TMDL object name, quoted when it holds anything beyond word characters."""
    return name if name.replace("_", "").isalnum() else f"'{name}'"


def _column_block(table: Table) -> list[str]:
    lines: list[str] = []
    for col in table.columns:
        fmt = {"dateTime": "yyyy-mm-dd", "int64": "0"}.get(col.dtype)
        lines += [
            f"\tcolumn {_q(col.name)}",
            f"\t\tdataType: {col.dtype}",
        ]
        if fmt:
            lines.append(f"\t\tformatString: {fmt}")
        lines += [
            f"\t\tlineageTag: {_tag(table.name, col.name)}",
            f"\t\tsummarizeBy: {col.summarize}",
            f"\t\tsourceColumn: {col.name}",
            "",
        ]
    return lines


def _partition_block(table: Table) -> list[str]:
    types = ", ".join(f'{{"{c.name}", {DTYPE_M[c.dtype]}}}' for c in table.columns)
    return [
        f"\tpartition {table.name} = m",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\tlet",
        "\t\t\t\t    Source = Csv.Document(",
        f'\t\t\t\t        File.Contents(DataFolder & "\\{table.name}.csv"),',
        '\t\t\t\t        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]',
        "\t\t\t\t    ),",
        "\t\t\t\t    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
        f'\t\t\t\t    Typed = Table.TransformColumnTypes(Promoted, {{{types}}}, "en-US")',
        "\t\t\t\tin",
        "\t\t\t\t    Typed",
        "",
    ]


def table_tmdl(table: Table) -> str:
    lines = [f"table {table.name}", f"\tlineageTag: {_tag(table.name)}", ""]
    for m in (m for m in MEASURES if m.table == table.name):
        lines += [
            f"\tmeasure {_q(m.name)} = {m.dax}",
            f"\t\tformatString: {m.format}",
            f"\t\tlineageTag: {_tag(table.name, 'measure', m.name)}",
            "",
        ]
    lines += _column_block(table)
    lines += _partition_block(table)
    lines.append("\tannotation PBI_ResultType = Table")
    return "\n".join(lines) + "\n"


def model_files() -> dict[str, str]:
    files: dict[str, str] = {
        "definition.pbism": json.dumps({"version": "4.0", "settings": {}}, indent=2) + "\n",
        "definition/database.tmdl": "database\n\tcompatibilityLevel: 1567\n",
        "definition/model.tmdl": "\n".join(
            [
                "model Model",
                "\tculture: en-US",
                "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
                "\tdiscourageImplicitMeasures",
                "",
                *[f"ref table {t.name}" for t in TABLES],
                "ref expression DataFolder",
                "",
            ]
        ),
        "definition/expressions.tmdl": (
            f'expression DataFolder = "{DATA_FOLDER_PLACEHOLDER}" '
            'meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]\n'
            f"\tlineageTag: {_tag('DataFolder')}\n"
        ),
    }
    rel_lines: list[str] = []
    for many, one in RELATIONSHIPS:
        rel_lines += [
            f"relationship {_tag('rel', many, one)}",
            f"\tfromColumn: {many}",
            f"\ttoColumn: {one}",
            "",
        ]
    files["definition/relationships.tmdl"] = "\n".join(rel_lines)
    for t in TABLES:
        files[f"definition/tables/{t.name}.tmdl"] = table_tmdl(t)
    return files


# --- report (PBIR-legacy report.json) -----------------------------------------------------------

Ref = tuple[str, str, str]  # (entity, property, "col" | "measure")


def _col(entity: str, prop: str) -> Ref:
    return (entity, prop, "col")


def _measure(name: str) -> Ref:
    entity = next(m.table for m in MEASURES if m.name == name)
    return (entity, name, "measure")


def _visual(
    name: str,
    visual_type: str,
    box: tuple[int, int, int, int],
    roles: dict[str, list[Ref]],
    title: str | None = None,
    objects: dict[str, Any] | None = None,
) -> dict[str, Any]:
    x, y, w, h = box
    aliases: dict[str, str] = {}
    select: list[dict[str, Any]] = []
    projections: dict[str, list[dict[str, str]]] = {}
    for role, refs in roles.items():
        for entity, prop, kind in refs:
            alias = aliases.setdefault(entity, f"t{len(aliases)}")
            query_ref = f"{entity}.{prop}"
            body = {"Expression": {"SourceRef": {"Source": alias}}, "Property": prop}
            select.append({("Column" if kind == "col" else "Measure"): body, "Name": query_ref})
            projections.setdefault(role, []).append({"queryRef": query_ref})
    single: dict[str, Any] = {
        "visualType": visual_type,
        "projections": projections,
        "prototypeQuery": {
            "Version": 2,
            "From": [{"Name": a, "Entity": e, "Type": 0} for e, a in aliases.items()],
            "Select": select,
        },
        "drillFilterOtherVisuals": True,
    }
    if objects:
        single["objects"] = objects
    if title:
        single["vcObjects"] = {
            "title": [
                {
                    "properties": {
                        "show": {"expr": {"Literal": {"Value": "true"}}},
                        "text": {"expr": {"Literal": {"Value": f"'{title}'"}}},
                    }
                }
            ]
        }
    config = {
        "name": name,
        "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": 0, "width": w, "height": h}}],
        "singleVisual": single,
    }
    return {
        "config": json.dumps(config, separators=(",", ":")),
        "filters": "[]",
        "height": h,
        "width": w,
        "x": x,
        "y": y,
        "z": 0,
    }


def _policy_slicer(page: str, default: str | None = None) -> dict[str, Any]:
    if default:
        return _single_select_slicer(
            f"{page}_policy",
            "dim_policy",
            "policy_code",
            default,
            "Reserve policy",
            (20, 20, 200, 160),
        )
    return _visual(
        f"{page}_policy",
        "slicer",
        (20, 20, 200, 160),
        {"Values": [_col("dim_policy", "policy_code")]},
        "Reserve policy",
    )


DEFAULT_QUARTER = "2024 Q4"  # the last quarter-end that has realized E&O
DEFAULT_POLICY = "D"  # SKU Drill shows one policy so per-SKU reserves are not summed across A-D


def _in_filter(entity: str, prop: str, values: list[str]) -> dict[str, Any]:
    """A Power BI categorical filter body: entity[prop] IN values (literals pre-encoded)."""
    column = {"Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": prop}}
    return {
        "Version": 2,
        "From": [{"Name": "d", "Entity": entity, "Type": 0}],
        "Where": [
            {
                "Condition": {
                    "In": {
                        "Expressions": [column],
                        "Values": [[{"Literal": {"Value": v}} for v in values]],
                    }
                }
            }
        ],
    }


def _single_select_slicer(
    name: str,
    entity: str,
    prop: str,
    default: str,
    title: str,
    box: tuple[int, int, int, int],
) -> dict[str, Any]:
    """Single-select slicer preselected to ``default``, so measures are not added up across the
    slicer's members."""
    objects = {
        "selection": [{"properties": {"singleSelect": {"expr": {"Literal": {"Value": "true"}}}}}],
        "general": [
            {"properties": {"filter": {"filter": _in_filter(entity, prop, [f"'{default}'"])}}}
        ],
    }
    return _visual(name, "slicer", box, {"Values": [_col(entity, prop)]}, title, objects)


def _quarter_slicer(page: str, box: tuple[int, int, int, int]) -> dict[str, Any]:
    return _single_select_slicer(
        f"{page}_quarter", "dim_date", "quarter_label", DEFAULT_QUARTER, "Quarter-end", box
    )


# Page-level filters. The calibration page pools the seven scored quarter-ends, so every measure
# on it (policy reserve, realized, coverage, under-reserve) covers the same snapshots.
PAGE_FILTERS: dict[str, list[dict[str, Any]]] = {
    "Backtest Calibration": [
        {
            "name": "scored_quarter_ends",
            "expression": {
                "Column": {
                    "Expression": {"SourceRef": {"Entity": "dim_date"}},
                    "Property": "is_scored",
                }
            },
            "filter": _in_filter("dim_date", "is_scored", ["1L"]),
            "type": "Categorical",
        }
    ]
}


def report_pages() -> list[tuple[str, list[dict[str, Any]]]]:
    quarter = _col("dim_date", "quarter_label")
    policy = _col("dim_policy", "policy_code")
    return [
        (
            "Reserve Trend",
            [
                _policy_slicer("trend"),
                _visual(
                    "trend_reserve",
                    "lineChart",
                    (240, 20, 1020, 330),
                    {"Category": [quarter], "Series": [policy], "Y": [_measure("Reserve $")]},
                    "Reserve by quarter-end and policy",
                ),
                _visual(
                    "trend_realized",
                    "clusteredColumnChart",
                    (240, 370, 640, 330),
                    {"Category": [quarter], "Y": [_measure("Realized E&O $")]},
                    "Realized E&O, stock unsold 12 months later",
                ),
                _visual(
                    "trend_supply",
                    "lineChart",
                    (900, 370, 360, 330),
                    {
                        "Category": [quarter],
                        "Y": [
                            _measure("Months of Supply (Forecast)"),
                            _measure("Months of Supply (Trailing)"),
                        ],
                    },
                    "Months of supply by quarter-end",
                ),
            ],
        ),
        (
            "Backtest Calibration",
            [
                _policy_slicer("calibration"),
                _visual(
                    "calibration_coverage",
                    "lineChart",
                    (240, 20, 500, 330),
                    {"Category": [quarter], "Series": [policy], "Y": [_measure("Coverage Ratio")]},
                    "Coverage ratio (reserve / realized)",
                ),
                _visual(
                    "calibration_under",
                    "clusteredColumnChart",
                    (760, 20, 500, 330),
                    {"Category": [quarter], "Series": [policy], "Y": [_measure("Under-reserve $")]},
                    "Under-reserve by quarter-end",
                ),
                _visual(
                    "calibration_table",
                    "tableEx",
                    (240, 370, 1020, 330),
                    {
                        "Values": [
                            policy,
                            _measure("Reserve $"),
                            _measure("Realized E&O $"),
                            _measure("Coverage Ratio"),
                            _measure("Under-reserve $"),
                            _measure("Flag Precision"),
                            _measure("Flag Recall"),
                        ]
                    },
                    "Calibration by policy",
                ),
            ],
        ),
        (
            "SKU Drill",
            [
                _policy_slicer("drill", DEFAULT_POLICY),
                _quarter_slicer("drill", (20, 420, 200, 280)),
                _visual(
                    "drill_cohort",
                    "slicer",
                    (20, 200, 200, 200),
                    {"Values": [_col("dim_sku", "cohort")]},
                    "Cohort",
                ),
                _visual(
                    "drill_table",
                    "tableEx",
                    (240, 20, 1020, 680),
                    {
                        "Values": [
                            _col("dim_sku", "sku_id"),
                            _col("dim_sku", "phone_model"),
                            _measure("Reserve $"),
                            _measure("Realized E&O $"),
                            _measure("Months of Supply (Forecast)"),
                            _measure("Months of Supply (Trailing)"),
                        ]
                    },
                    "SKU detail",
                ),
            ],
        ),
    ]


def report_files() -> dict[str, str]:
    sections = []
    for i, (title, visuals) in enumerate(report_pages()):
        sections.append(
            {
                "config": "{}",
                "displayName": title,
                "displayOption": 1,
                "filters": json.dumps(PAGE_FILTERS.get(title, []), separators=(",", ":")),
                "height": 720,
                "name": f"ReportSection{i + 1}",
                "visualContainers": visuals,
                "width": 1280,
            }
        )
    report = {
        "config": json.dumps(
            {
                "version": "5.59",
                "themeCollection": {
                    "baseTheme": {"name": "CY24SU10", "version": "5.59", "type": 2}
                },
                "activeSectionIndex": 0,
                "defaultDrillFilterOtherVisuals": True,
            },
            separators=(",", ":"),
        ),
        "layoutOptimization": 0,
        "resourcePackages": [
            {
                "resourcePackage": {
                    "disabled": False,
                    "items": [
                        {"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json", "type": 202}
                    ],
                    "name": "SharedResources",
                    "type": 2,
                }
            }
        ],
        "sections": sections,
    }
    pbir = {
        "version": PBIR_LEGACY_VERSION,
        "datasetReference": {
            "byPath": {"path": f"../{PROJECT}.SemanticModel"},
            "byConnection": None,
        },
    }
    return {
        "definition.pbir": json.dumps(pbir, indent=2) + "\n",
        "report.json": json.dumps(report, indent=2) + "\n",
    }


def project_files() -> dict[str, str]:
    """Every file of the PBIP project, keyed by path relative to the ``powerbi`` folder."""
    pbip = {
        "version": "1.0",
        "artifacts": [{"report": {"path": f"{PROJECT}.Report"}}],
        "settings": {"enableAutoRecovery": True},
    }
    files = {f"{PROJECT}.pbip": json.dumps(pbip, indent=2) + "\n"}
    for name, text in model_files().items():
        files[f"{PROJECT}.SemanticModel/{name}"] = text
    for name, text in report_files().items():
        files[f"{PROJECT}.Report/{name}"] = text
    return files


def write_project(root: Path) -> list[Path]:
    written = []
    for rel, text in project_files().items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        written.append(path)
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "powerbi")
    for p in write_project(target):
        print(p)
