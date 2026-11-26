-- Calibration metrics per policy and quarter-end, for snapshots with realized E&O.
CREATE OR REPLACE TABLE backtest_metrics AS
SELECT
    r.policy,
    r.snapshot_date,
    sum(r.reserve_usd) AS reserve_usd,
    sum(z.realized_usd) AS realized_usd,
    sum(greatest(0, z.realized_usd - r.reserve_usd)) AS under_reserve_usd,
    count(*) FILTER (WHERE r.reserve_usd > 0 AND z.realized_units > 0) AS true_pos,
    count(*) FILTER (WHERE r.reserve_usd > 0 AND z.realized_units = 0) AS false_pos,
    count(*) FILTER (WHERE r.reserve_usd = 0 AND z.realized_units > 0) AS false_neg,
    count(*) FILTER (WHERE r.reserve_usd = 0 AND z.realized_units = 0) AS true_neg
FROM reserve_asof AS r
JOIN realized_asof AS z USING (snapshot_date, sku_id)
GROUP BY r.policy, r.snapshot_date
