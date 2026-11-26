-- Realized E&O: stock on hand at the snapshot that was not sold in the next 12 months.
-- Sell-through consumes snapshot stock first (a stated simplification).
INSERT INTO realized_asof
WITH sold AS (
    SELECT sku_id, sum(units_sold) AS units_sold
    FROM sales
    WHERE sales_month > date_trunc('month', $snap::DATE)
      AND sales_month <= date_trunc('month', $snap::DATE) + INTERVAL 12 MONTH
    GROUP BY sku_id
)
SELECT
    s.snapshot_date,
    s.sku_id,
    s.on_hand_units,
    s.unit_cost,
    coalesce(d.units_sold, 0) AS units_sold_next_12m,
    greatest(0, s.on_hand_units - coalesce(d.units_sold, 0)) AS realized_units,
    greatest(0, s.on_hand_units - coalesce(d.units_sold, 0)) * s.unit_cost AS realized_usd
FROM inventory_snapshot AS s
LEFT JOIN sold AS d USING (sku_id)
WHERE s.snapshot_date = $snap::DATE
