-- One row per day, Bundesland and fuel type.
SELECT snapshot_date, bundesland_code, fuel_type, count(*) AS duplicates
FROM mart.fuel_price_daily
GROUP BY snapshot_date, bundesland_code, fuel_type
HAVING count(*) > 1;
