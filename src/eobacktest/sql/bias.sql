-- Forecast bias by lifecycle stage of the target month: every regular monthly version against
-- the sales that followed. Versions dated off month-end (restatements) are not regular.
CREATE OR REPLACE TABLE forecast_bias AS
WITH f AS (
    SELECT sku_id, version_date, target_month, forecast_units
    FROM forecast_version
    WHERE version_date = last_day(version_date)
      AND target_month <= (SELECT max(sales_month) FROM sales)
),
staged AS (
    SELECT f.sku_id, f.version_date, f.target_month, f.forecast_units, l.stage
    FROM f
    JOIN lifecycle AS l ON l.sku_id = f.sku_id AND l.from_month <= f.target_month
    QUALIFY row_number() OVER (
        PARTITION BY f.sku_id, f.version_date, f.target_month ORDER BY l.from_month DESC
    ) = 1
)
SELECT
    st.stage,
    count(*) AS n,
    sum(st.forecast_units) AS forecast_units,
    sum(a.units_sold) AS actual_units,
    CASE WHEN sum(a.units_sold) > 0
         THEN sum(st.forecast_units) / sum(a.units_sold) - 1 END AS bias
FROM staged AS st
JOIN sales AS a ON a.sku_id = st.sku_id AND a.sales_month = st.target_month
GROUP BY st.stage
