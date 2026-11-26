"""Star schema shared by the CSV export and the Power BI semantic model.

Each ``Table`` carries the DuckDB query that produces its CSV and the column list the TMDL
file declares, so the export and the model cannot drift apart without a test noticing.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config as C

DTYPE_M = {
    "int64": "Int64.Type",
    "string": "type text",
    "double": "type number",
    "dateTime": "type date",
}

_SKU_KEY = "CAST(substr(sku_id, 5) AS INTEGER)"
_DATE_KEY = "CAST(strftime(snapshot_date, '%Y%m%d') AS INTEGER)"


@dataclass(frozen=True)
class Column:
    name: str
    dtype: str  # int64 | string | double | dateTime
    summarize: str = "none"  # TMDL summarizeBy


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    query: str
    key: str | None = None  # primary key of a dimension

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


def _policy_values() -> str:
    rows = []
    for code in C.POLICIES:
        key = ord(code) - 64
        name = C.POLICY_NAMES[code].replace("'", "''")
        desc = C.POLICY_DESCRIPTIONS[code].replace("'", "''")
        rows.append(f"({key}, '{code}', '{name}', '{desc}')")
    return ",\n        ".join(rows)


TABLES: tuple[Table, ...] = (
    Table(
        "dim_sku",
        (
            Column("sku_key", "int64"),
            Column("sku_id", "string"),
            Column("name", "string"),
            Column("phone_model", "string"),
            Column("category", "string"),
            Column("kind", "string"),
            Column("unit_cost", "double"),
            Column("cohort", "string"),
        ),
        """
        SELECT CAST(substr(s.sku_id, 5) AS INTEGER) AS sku_key, s.sku_id, s.name, s.phone_model,
               s.category, s.kind, s.unit_cost,
               CASE
                   WHEN s.kind = 'gift_pack' THEN 'Seasonal gift pack'
                   WHEN pm.discontinued_month IS NOT NULL THEN pm.model_id || ' end-of-life'
                   WHEN EXISTS (SELECT 1 FROM lifecycle AS l
                                WHERE l.sku_id = s.sku_id AND l.stage = 'dormant')
                       THEN 'Dormant stock'
                   ELSE 'Core'
               END AS cohort
        FROM sku AS s JOIN phone_model AS pm ON pm.model_id = s.phone_model
        ORDER BY sku_key
        """,
        key="sku_key",
    ),
    Table(
        "dim_date",
        (
            Column("date_key", "int64"),
            Column("date", "dateTime"),
            Column("year", "int64"),
            Column("quarter_label", "string"),
            Column("month_number", "int64"),
            Column("is_quarter_end", "int64"),
            Column("is_scored", "int64"),
        ),
        """
        SELECT CAST(strftime(d, '%Y%m%d') AS INTEGER) AS date_key, d AS date, year(d) AS year,
               year(d) || ' Q' || quarter(d) AS quarter_label, month(d) AS month_number,
               CASE WHEN month(d) % 3 = 0 THEN 1 ELSE 0 END AS is_quarter_end,
               CASE WHEN EXISTS (SELECT 1 FROM realized_asof AS z WHERE z.snapshot_date = d)
                    THEN 1 ELSE 0 END AS is_scored
        FROM (SELECT DISTINCT last_day(sales_month) AS d FROM sales)
        ORDER BY d
        """,
        key="date_key",
    ),
    Table(
        "dim_policy",
        (
            Column("policy_key", "int64"),
            Column("policy_code", "string"),
            Column("policy_name", "string"),
            Column("description", "string"),
        ),
        f"""
        SELECT * FROM (VALUES
        {_policy_values()}
        ) AS t(policy_key, policy_code, policy_name, description)
        ORDER BY policy_key
        """,
        key="policy_key",
    ),
    Table(
        "fct_reserve",
        (
            Column("sku_key", "int64"),
            Column("date_key", "int64"),
            Column("policy_key", "int64"),
            Column("reserve_units", "double", "sum"),
            Column("reserve_usd", "double", "sum"),
            Column("under_reserve_usd", "double", "sum"),
        ),
        f"""
        SELECT {_SKU_KEY} AS sku_key, {_DATE_KEY} AS date_key,
               ascii(r.policy) - 64 AS policy_key,
               round(r.reserve_units, 4) AS reserve_units,
               round(r.reserve_usd, 4) AS reserve_usd,
               round(greatest(0, z.realized_usd - r.reserve_usd), 4) AS under_reserve_usd
        FROM reserve_asof AS r
        LEFT JOIN realized_asof AS z USING (snapshot_date, sku_id)
        ORDER BY sku_key, date_key, policy_key
        """,
    ),
    Table(
        "fct_realized",
        (
            Column("sku_key", "int64"),
            Column("date_key", "int64"),
            Column("on_hand_units", "double", "sum"),
            Column("unit_cost", "double"),
            Column("on_hand_usd", "double", "sum"),
            Column("forecast_12m_units", "double", "sum"),
            Column("trailing_avg_usage", "double", "sum"),
            Column("days_since_movement", "int64"),
            Column("units_sold_next_12m", "double", "sum"),
            Column("realized_units", "double", "sum"),
            Column("realized_usd", "double", "sum"),
        ),
        f"""
        SELECT {_SKU_KEY} AS sku_key, {_DATE_KEY} AS date_key,
               p.on_hand_units, p.unit_cost,
               round(p.on_hand_units * p.unit_cost, 4) AS on_hand_usd,
               round(p.forecast_12m_units, 4) AS forecast_12m_units,
               round(p.trailing_avg_usage, 4) AS trailing_avg_usage,
               p.days_since_movement,
               z.units_sold_next_12m, z.realized_units,
               round(z.realized_usd, 4) AS realized_usd
        FROM position_asof AS p
        LEFT JOIN realized_asof AS z USING (snapshot_date, sku_id)
        ORDER BY sku_key, date_key
        """,
    ),
    Table(
        "fct_backtest",
        (
            Column("policy_key", "int64"),
            Column("date_key", "int64"),
            Column("reserve_usd", "double", "sum"),
            Column("realized_usd", "double", "sum"),
            Column("under_reserve_usd", "double", "sum"),
            Column("true_pos", "int64", "sum"),
            Column("false_pos", "int64", "sum"),
            Column("false_neg", "int64", "sum"),
            Column("true_neg", "int64", "sum"),
        ),
        f"""
        SELECT ascii(policy) - 64 AS policy_key, {_DATE_KEY} AS date_key,
               round(reserve_usd, 4) AS reserve_usd, round(realized_usd, 4) AS realized_usd,
               round(under_reserve_usd, 4) AS under_reserve_usd,
               true_pos, false_pos, false_neg, true_neg
        FROM backtest_metrics
        ORDER BY policy_key, date_key
        """,
    ),
)

TABLE_BY_NAME = {t.name: t for t in TABLES}

# Relationships: (many-side table.column, one-side table.column)
RELATIONSHIPS: tuple[tuple[str, str], ...] = (
    ("fct_reserve.sku_key", "dim_sku.sku_key"),
    ("fct_reserve.date_key", "dim_date.date_key"),
    ("fct_reserve.policy_key", "dim_policy.policy_key"),
    ("fct_realized.sku_key", "dim_sku.sku_key"),
    ("fct_realized.date_key", "dim_date.date_key"),
    ("fct_backtest.policy_key", "dim_policy.policy_key"),
    ("fct_backtest.date_key", "dim_date.date_key"),
)
