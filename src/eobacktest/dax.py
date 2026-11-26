"""A small reader for the DAX subset the model uses.

``references`` pulls column and measure references out of an expression (used by the static
validator). ``evaluate`` computes the value of an expression with no filter context, given a
function that sums a column; tests use it to prove each measure equals its SQL reference.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import duckdb

from .schema import RELATIONSHIPS, TABLE_BY_NAME

_COLUMN_REF = re.compile(r"\b([A-Za-z_]\w*)\[([^\]]+)\]")
_MEASURE_REF = re.compile(r"(?<![\w\]])\[([^\]]+)\]")
_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([A-Za-z_]\w*\[[^\]]+\])|(\[[^\]]+\])|(\w+)|(.))")


def references(dax: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Return (table, column) references and bare [Measure] references of an expression."""
    columns = [(m.group(1), m.group(2)) for m in _COLUMN_REF.finditer(dax)]
    measures = [m.group(1) for m in _MEASURE_REF.finditer(dax)]
    return columns, measures


class _Parser:
    def __init__(
        self,
        dax: str,
        column_sum: Callable[[str, str], float],
        measure: Callable[[str], float],
    ) -> None:
        self.tokens = [t for t in self._lex(dax)]
        self.pos = 0
        self.column_sum = column_sum
        self.measure = measure

    @staticmethod
    def _lex(dax: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for m in _TOKEN.finditer(dax):
            number, colref, mref, word, punct = m.groups()
            if number:
                out.append(("num", number))
            elif colref:
                out.append(("col", colref))
            elif mref:
                out.append(("measure", mref[1:-1]))
            elif word:
                out.append(("word", word))
            elif punct:
                out.append(("punct", punct))
        return out

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self, expected: str | None = None) -> tuple[str, str]:
        tok = self.peek()
        if tok is None or (expected is not None and tok[1] != expected):
            raise ValueError(f"unexpected token {tok!r}, wanted {expected!r}")
        self.pos += 1
        return tok

    def expr(self) -> float:
        value = self.term()
        while (tok := self.peek()) and tok[1] in "+-":
            self.take()
            rhs = self.term()
            value = value + rhs if tok[1] == "+" else value - rhs
        return value

    def term(self) -> float:
        value = self.factor()
        while (tok := self.peek()) and tok[1] in "*/":
            self.take()
            rhs = self.factor()
            value = value * rhs if tok[1] == "*" else (value / rhs if rhs else float("nan"))
        return value

    def factor(self) -> float:
        kind, text = self.take()
        if kind == "num":
            return float(text)
        if kind == "measure":
            return self.measure(text)
        if kind == "punct" and text == "(":
            value = self.expr()
            self.take(")")
            return value
        if kind == "word" and text == "SUM":
            self.take("(")
            _, ref = self.take()
            self.take(")")
            table, column = ref[:-1].split("[", 1)
            return self.column_sum(table, column)
        if kind == "word" and text == "DIVIDE":
            self.take("(")
            num = self.expr()
            self.take(",")
            den = self.expr()
            self.take(")")
            return num / den if den else float("nan")
        raise ValueError(f"unsupported DAX construct {text!r}")


def evaluate(
    name: str,
    measures: dict[str, str],
    column_sum: Callable[[str, str], float],
) -> float:
    """Evaluate measure ``name`` (other measures resolve through ``measures``)."""

    def resolve(measure_name: str) -> float:
        return evaluate(measure_name, measures, column_sum)

    parser = _Parser(measures[name], column_sum, resolve)
    value = parser.expr()
    if parser.peek() is not None:
        raise ValueError(f"trailing tokens in measure {name!r}")
    return value


Filter = tuple[str, str, list]  # (dimension table, column, allowed values)


def csv_column_sum(
    data_dir: Path, filters: list[Filter] | None = None
) -> Callable[[str, str], float]:
    """Return a ``column_sum`` that sums a CSV column under a filter context.

    A filter on a dimension reaches a fact table only through a model relationship, as in Power
    BI: ``fct_realized`` has none to ``dim_policy``, so a policy filter leaves it unfiltered.
    """
    con = duckdb.connect()
    filters = filters or []

    def column_sum(table: str, column: str) -> float:
        path = data_dir / f"{table}.csv"
        joins, wheres = [], []
        for i, (dim, col, values) in enumerate(filters):
            if dim == table:
                wheres.append((None, col, values))
                continue
            for many, one in RELATIONSHIPS:
                m_table, m_col = many.split(".")
                o_table, o_col = one.split(".")
                if m_table == table and o_table == dim:
                    alias = f"d{i}"
                    joins.append(
                        f"JOIN read_csv('{data_dir / (dim + '.csv')}') AS {alias} "
                        f"ON f.{m_col} = {alias}.{o_col}"
                    )
                    wheres.append((alias, col, values))
        conds = []
        for alias, col, values in wheres:
            ref = f"{alias}.{col}" if alias else f"f.{col}"
            literals = ", ".join(repr(v) for v in values)
            conds.append(f"{ref} IN ({literals})")
        query = (
            f"SELECT sum(f.{column}) FROM read_csv('{path}') AS f "
            + " ".join(joins)
            + (" WHERE " + " AND ".join(conds) if conds else "")
        )
        row = con.execute(query).fetchone()
        assert row is not None and TABLE_BY_NAME[table]
        return float(row[0]) if row[0] is not None else float("nan")

    return column_sum
