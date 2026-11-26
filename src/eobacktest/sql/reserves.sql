-- Reserves under policies A-D, a pure function of the point-in-time position rows.
INSERT INTO reserve_asof
WITH p AS (
    SELECT
        snapshot_date,
        sku_id,
        unit_cost,
        on_hand_units,
        greatest(0, on_hand_units - forecast_12m_units) * unit_cost AS a_usd,
        greatest(0, on_hand_units - 12 * trailing_avg_usage) * unit_cost AS b_usd,
        on_hand_units * unit_cost * CASE
            WHEN days_since_movement > 365 THEN 1.0
            WHEN days_since_movement > 180 THEN 0.5
            ELSE 0.0
        END AS c_usd
    FROM position_asof
    WHERE snapshot_date = $snap::DATE
),
r AS (
    SELECT *, greatest(b_usd, c_usd) AS d_usd FROM p
)
SELECT snapshot_date, sku_id, 'A', a_usd / unit_cost, a_usd FROM r
UNION ALL
SELECT snapshot_date, sku_id, 'B', b_usd / unit_cost, b_usd FROM r
UNION ALL
SELECT snapshot_date, sku_id, 'C', c_usd / unit_cost, c_usd FROM r
UNION ALL
SELECT snapshot_date, sku_id, 'D', d_usd / unit_cost, d_usd FROM r
