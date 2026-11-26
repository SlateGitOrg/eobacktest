"""Static validation of the Power BI project, with no Power BI installation.

Checks: every model column maps to a CSV header; every DAX reference resolves; the measure set
equals the registry; relationship columns exist, are unique on the one side and have no orphans;
every visual binding resolves to a model column or measure; every page carries a policy slicer.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import dax
from .registry import MEASURE_NAMES

REQUIRED_PAGES = ("Reserve Trend", "Backtest Calibration", "SKU Drill")

_MEASURE_LINE = re.compile(r"^\tmeasure (?:'((?:[^']|'')+)'|(\S+)) = (.*)$")
_COLUMN_LINE = re.compile(r"^\tcolumn (?:'((?:[^']|'')+)'|(\S+))\s*$")
_SOURCE_COLUMN = re.compile(r"^\t\tsourceColumn: (.+)$")
_CSV_NAME = re.compile(r'DataFolder & "\\([A-Za-z0-9_]+)\.csv"')


@dataclass
class ModelTable:
    name: str
    columns: dict[str, str] = field(default_factory=dict)  # column name -> sourceColumn
    measures: dict[str, str] = field(default_factory=dict)  # measure name -> DAX
    csv_name: str | None = None


def _unquote(quoted: str | None, bare: str | None) -> str:
    return quoted.replace("''", "'") if quoted is not None else str(bare)


def parse_table(text: str) -> ModelTable:
    lines = text.splitlines()
    first = lines[0].split(" ", 1)
    table = ModelTable(name=first[1].strip().strip("'"))
    current: str | None = None
    for line in lines[1:]:
        if m := _MEASURE_LINE.match(line):
            table.measures[_unquote(m.group(1), m.group(2))] = m.group(3).strip()
            current = None
        elif m := _COLUMN_LINE.match(line):
            current = _unquote(m.group(1), m.group(2))
            table.columns[current] = current
        elif current and (m := _SOURCE_COLUMN.match(line)):
            table.columns[current] = m.group(1).strip()
        elif m := _CSV_NAME.search(line):
            table.csv_name = m.group(1)
    return table


def parse_relationships(text: str) -> list[tuple[str, str]]:
    rels: list[tuple[str, str]] = []
    src = dst = None
    for line in text.splitlines():
        if line.startswith("relationship"):
            if src and dst:
                rels.append((src, dst))
            src = dst = None
        elif line.strip().startswith("fromColumn:"):
            src = line.split(":", 1)[1].strip()
        elif line.strip().startswith("toColumn:"):
            dst = line.split(":", 1)[1].strip()
    if src and dst:
        rels.append((src, dst))
    return rels


def _csv_header(path: Path) -> list[str]:
    with path.open(newline="") as fh:
        return next(csv.reader(fh), [])


def _csv_column(path: Path, column: str) -> list[str]:
    with path.open(newline="") as fh:
        return [row[column] for row in csv.DictReader(fh)]


def _literal(value: str) -> str | int:
    """Decode a Power BI literal: 'text' or 1L."""
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return int(value.rstrip("L")) if value.rstrip("L").lstrip("-").isdigit() else value


def _in_conditions(filter_body: dict[str, Any]) -> list[tuple[str, str, list[str | int]]]:
    """(entity, property, values) for each IN condition of a categorical filter body."""
    aliases = {f["Name"]: f["Entity"] for f in filter_body.get("From", [])}
    out = []
    for where in filter_body.get("Where", []):
        cond = where["Condition"]["In"]
        column = cond["Expressions"][0]["Column"]
        entity = aliases.get(column["Expression"]["SourceRef"].get("Source", ""), "")
        values = [_literal(v["Literal"]["Value"]) for v in cond["Values"][0]]
        out.append((entity, column["Property"], values))
    return out


def page_filters(section: dict[str, Any]) -> list[tuple[str, str, list[str | int]]]:
    """Page-level categorical filters of a report section as (table, column, values)."""
    out: list[tuple[str, str, list[str | int]]] = []
    for flt in json.loads(section.get("filters") or "[]"):
        out += _in_conditions(flt["filter"])
    return out


def slicer_default(section: dict[str, Any], field: str) -> list[str | int] | None:
    """Preselected values of a single-select slicer on ``field`` ("table.column"), if any."""
    for container in section.get("visualContainers", []):
        single = json.loads(container["config"])["singleVisual"]
        if single["visualType"] != "slicer":
            continue
        fields = [(r["queryRef"]) for refs in single["projections"].values() for r in refs]
        if fields != [field]:
            continue
        objects = single.get("objects", {})
        try:
            single_select = objects["selection"][0]["properties"]["singleSelect"]
            default = objects["general"][0]["properties"]["filter"]["filter"]
        except (KeyError, IndexError):
            return None
        if single_select["expr"]["Literal"]["Value"] != "true":
            return None
        conds = _in_conditions(default)
        return conds[0][2] if conds else None
    return None


def quarter_slicer_default(section: dict[str, Any]) -> list[str | int] | None:
    return slicer_default(section, "dim_date.quarter_label")


def policy_slicer_default(section: dict[str, Any]) -> list[str | int] | None:
    return slicer_default(section, "dim_policy.policy_code")


def _check_visuals(
    report: dict[str, Any],
    tables: dict[str, ModelTable],
    quarter_labels: set[str] | None,
    policy_codes: set[str] | None,
    policy_facts: set[str],
) -> list[str]:
    errors: list[str] = []
    pages = [s.get("displayName") for s in report.get("sections", [])]
    if tuple(pages) != REQUIRED_PAGES:
        errors.append(f"report pages are {pages}, expected {list(REQUIRED_PAGES)}")
    for section in report.get("sections", []):
        page = section.get("displayName")
        has_policy_slicer = False
        filters = page_filters(section)
        for entity, prop, _values in filters:
            if entity not in tables or prop not in tables[entity].columns:
                errors.append(f"page {page!r}: page filter on missing column {entity}[{prop}]")
        scored_only = ("dim_date", "is_scored", [1]) in filters
        default_quarter = quarter_slicer_default(section)
        default_policy = policy_slicer_default(section)
        for default, known, label in (
            (default_quarter, quarter_labels, "dim_date[quarter_label]"),
            (default_policy, policy_codes, "dim_policy[policy_code]"),
        ):
            if default is not None and known is not None:
                for value in default:
                    if value not in known:
                        errors.append(
                            f"page {page!r}: slicer default {value!r} is not a {label} value"
                        )
        for container in section.get("visualContainers", []):
            cfg = json.loads(container["config"])
            name = cfg.get("name", "?")
            single = cfg["singleVisual"]
            query = single["prototypeQuery"]
            aliases = {f["Name"]: f["Entity"] for f in query["From"]}
            names = set()
            uses_measure = False
            uses_date = False
            uses_policy = False
            policy_measures: list[str] = []
            for item in query["Select"]:
                kind = "Column" if "Column" in item else "Measure"
                body = item[kind]
                entity = aliases.get(body["Expression"]["SourceRef"]["Source"])
                prop = body["Property"]
                names.add(item["Name"])
                uses_measure |= kind == "Measure"
                uses_date |= entity == "dim_date"
                uses_policy |= entity == "dim_policy"
                if kind == "Measure" and entity in policy_facts:
                    policy_measures.append(prop)
                table = tables.get(entity or "")
                if table is None:
                    errors.append(f"page {page!r} visual {name}: unknown table {entity!r}")
                elif kind == "Column" and prop not in table.columns:
                    errors.append(f"page {page!r} visual {name}: missing column {entity}[{prop}]")
                elif kind == "Measure" and prop not in table.measures:
                    errors.append(f"page {page!r} visual {name}: missing measure {entity}[{prop}]")
                if (
                    single["visualType"] == "slicer"
                    and entity == "dim_policy"
                    and prop == "policy_code"
                ):
                    has_policy_slicer = True
            if uses_measure and not (uses_date or default_quarter or scored_only):
                errors.append(
                    f"page {page!r} visual {name}: measures add up across quarter-ends; put "
                    "dim_date on the visual, add a single-select quarter slicer with a default, "
                    "or filter the page to dim_date[is_scored] = 1"
                )
            if policy_measures and not (uses_policy or default_policy):
                errors.append(
                    f"page {page!r} visual {name}: {policy_measures} are summed across policies "
                    "A-D; put dim_policy on the visual or add a single-select policy slicer "
                    "with a default"
                )
            for role, refs in single["projections"].items():
                for ref in refs:
                    if ref["queryRef"] not in names:
                        errors.append(
                            f"page {page!r} visual {name}: {role} binds {ref['queryRef']!r}, "
                            "which is not in the query"
                        )
        if not has_policy_slicer:
            errors.append(f"page {page!r} has no slicer on dim_policy[policy_code]")
    return errors


def validate_project(pbip: Path, data_dir: Path) -> list[str]:
    """Return a list of problems; an empty list means the project passes."""
    errors: list[str] = []
    root = pbip.parent
    name = pbip.stem
    model_dir = root / f"{name}.SemanticModel" / "definition"
    report_dir = root / f"{name}.Report"

    if not pbip.exists():
        return [f"{pbip} not found"]
    try:
        artifacts = json.loads(pbip.read_text()).get("artifacts", [])
        report_path = artifacts[0]["report"]["path"]
        if not (root / report_path).is_dir():
            errors.append(f".pbip points at {report_path}, which does not exist")
        pbir = json.loads((report_dir / "definition.pbir").read_text())
        expected = "1.0" if (report_dir / "report.json").exists() else "4.0"
        if (report_dir / "definition").is_dir():
            expected = "4.0"
        if pbir.get("version") != expected:
            errors.append(
                f"definition.pbir version is {pbir.get('version')!r}; the "
                f"{'legacy report.json' if expected == '1.0' else 'definition/ folder'} "
                f"layout needs {expected!r}"
            )
        target = (report_dir / pbir["datasetReference"]["byPath"]["path"]).resolve()
        if not (target / "definition").is_dir():
            errors.append(f"definition.pbir points at {target.name}, which has no definition/")
    except (OSError, KeyError, IndexError, json.JSONDecodeError) as exc:
        errors.append(f"project files unreadable: {exc!r}")

    tables: dict[str, ModelTable] = {}
    for path in sorted((model_dir / "tables").glob("*.tmdl")):
        table = parse_table(path.read_text())
        tables[table.name] = table
    if not tables:
        return errors + [f"no tables found under {model_dir / 'tables'}"]

    for tname in ("database.tmdl", "model.tmdl", "relationships.tmdl", "expressions.tmdl"):
        if not (model_dir / tname).exists():
            errors.append(f"{tname} missing from the semantic model definition")
    expressions = model_dir / "expressions.tmdl"
    if expressions.exists() and "expression DataFolder" not in expressions.read_text():
        errors.append("expressions.tmdl does not define the DataFolder parameter")
    model_file = model_dir / "model.tmdl"
    if model_file.exists():
        listed = set(re.findall(r"^ref table (\S+)", model_file.read_text(), re.M))
        if listed != set(tables):
            errors.append(
                f"model.tmdl lists tables {sorted(listed)}, files define {sorted(tables)}"
            )

    # Columns against the exported CSV headers.
    headers: dict[str, list[str]] = {}
    for table in tables.values():
        csv_path = data_dir / f"{table.csv_name or table.name}.csv"
        if not csv_path.exists():
            errors.append(f"table {table.name}: data file {csv_path.name} not found in {data_dir}")
            continue
        headers[table.name] = _csv_header(csv_path)
        for column, source in table.columns.items():
            if source not in headers[table.name]:
                errors.append(
                    f"table {table.name}: column {column} reads {source!r}, "
                    f"which is not in the {csv_path.name} header"
                )

    # Measures: set equality with the registry, then every reference.
    defined = {m: t.name for t in tables.values() for m in t.measures}
    missing = sorted(MEASURE_NAMES - set(defined))
    extra = sorted(set(defined) - MEASURE_NAMES)
    if missing or extra:
        errors.append(
            f"measure set differs from the registry: missing {missing}, unexpected {extra}"
        )
    for table in tables.values():
        for measure, expr in table.measures.items():
            columns, measure_refs = dax.references(expr)
            for ref_table, ref_col in columns:
                if ref_table not in tables or ref_col not in tables[ref_table].columns:
                    errors.append(
                        f"measure {measure!r} references missing column {ref_table}[{ref_col}]"
                    )
            for ref in measure_refs:
                if ref not in defined:
                    errors.append(f"measure {measure!r} references unknown measure [{ref}]")

    # Relationships.
    rel_file = model_dir / "relationships.tmdl"
    for src, dst in parse_relationships(rel_file.read_text() if rel_file.exists() else ""):
        ends = []
        for end in (src, dst):
            t_name, _, c_name = end.partition(".")
            if t_name not in tables or c_name not in tables[t_name].columns:
                errors.append(f"relationship {src} -> {dst}: column {end} does not exist")
            else:
                ends.append((t_name, c_name))
        if len(ends) != 2 or any(t not in headers for t, _ in ends):
            continue
        (st, sc), (dt, dc) = ends
        one_values = _csv_column(data_dir / f"{tables[dt].csv_name or dt}.csv", dc)
        dupes = [v for v, n in Counter(one_values).items() if n > 1]
        if dupes:
            errors.append(
                f"relationship {src} -> {dst}: {dt}.{dc} is not unique on the one side "
                f"(value {dupes[0]} repeats)"
            )
        known = set(one_values)
        many_values = _csv_column(data_dir / f"{tables[st].csv_name or st}.csv", sc)
        orphans = sorted({v for v in many_values if v not in known})
        if orphans:
            errors.append(
                f"relationship {src} -> {dst}: {len(orphans)} values have no match in {dt}.{dc} "
                f"(first: {orphans[0]})"
            )

    # Report visuals.
    try:
        report = json.loads((report_dir / "report.json").read_text())
        labels: set[str] | None = None
        date_table = tables.get("dim_date")
        date_csv = data_dir / f"{(date_table.csv_name if date_table else None) or 'dim_date'}.csv"
        if date_csv.exists() and "quarter_label" in headers.get("dim_date", []):
            labels = set(_csv_column(date_csv, "quarter_label"))
        codes: set[str] | None = None
        policy_table = tables.get("dim_policy")
        policy_csv = (
            data_dir / f"{(policy_table.csv_name if policy_table else None) or 'dim_policy'}.csv"
        )
        if policy_csv.exists() and "policy_code" in headers.get("dim_policy", []):
            codes = set(_csv_column(policy_csv, "policy_code"))
        policy_facts = {
            src.split(".")[0]
            for src, dst in parse_relationships(rel_file.read_text() if rel_file.exists() else "")
            if dst.startswith("dim_policy.")
        }
        errors += _check_visuals(report, tables, labels, codes, policy_facts)
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        errors.append(f"report.json unreadable: {exc!r}")
    return errors
