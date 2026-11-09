-- Every Bundesland-day present in either source must appear in the snapshot (the
-- FULL OUTER JOIN must not lose rows). Returns the keys that are missing.
WITH expected AS (
    SELECT bundesland_code, obs_date AS date FROM mart.weather_daily_bundesland
    UNION
    SELECT bundesland_code, snapshot_date FROM mart.fuel_price_daily
)
SELECT e.bundesland_code, e.date
FROM expected AS e
LEFT JOIN mart.regional_daily_snapshot AS s
    ON s.bundesland_code = e.bundesland_code AND s.date = e.date
WHERE s.date IS NULL;
