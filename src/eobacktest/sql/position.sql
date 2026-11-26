-- Point-in-time position of every SKU at the snapshot date $snap.
-- Only forecast versions with version_date <= $snap and sales up to the snapshot month are read.
INSERT INTO position_asof
WITH snap AS (
    SELECT snapshot_date, sku_id, on_hand_units, unit_cost, last_movement_date
    FROM inventory_snapshot
    WHERE snapshot_date = $snap::DATE
),
current_version AS (
    SELECT sku_id, max(version_date) AS version_date
    FROM forecast_version
    WHERE version_date <= $snap::DATE
    GROUP BY sku_id
),
forecast AS (
    SELECT f.sku_id, c.version_date, sum(f.forecast_units) AS forecast_12m_units
    FROM forecast_version AS f
    JOIN current_version AS c ON c.sku_id = f.sku_id AND c.version_date = f.version_date
    GROUP BY f.sku_id, c.version_date
),
usage AS (
    SELECT sku_id, sum(units_sold) / 6.0 AS trailing_avg_usage
    FROM sales
    WHERE sales_month <= date_trunc('month', $snap::DATE)
      AND sales_month > date_trunc('month', $snap::DATE) - INTERVAL 6 MONTH
    GROUP BY sku_id
)
SELECT
    s.snapshot_date,
    s.sku_id,
    s.on_hand_units,
    s.unit_cost,
    s.last_movement_date,
    date_diff('day', s.last_movement_date, s.snapshot_date) AS days_since_movement,
    f.version_date AS forecast_version_date,
    coalesce(f.forecast_12m_units, 0) AS forecast_12m_units,
    coalesce(u.trailing_avg_usage, 0) AS trailing_avg_usage
FROM snap AS s
LEFT JOIN forecast AS f USING (sku_id)
LEFT JOIN usage AS u USING (sku_id)
