-- Point-in-time guard: position rows whose forecast version is dated after the snapshot.
-- Must return zero rows; `build` fails otherwise.
SELECT snapshot_date, sku_id, forecast_version_date
FROM position_asof
WHERE forecast_version_date > snapshot_date
ORDER BY snapshot_date, sku_id
