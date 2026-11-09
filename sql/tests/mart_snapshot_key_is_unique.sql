-- The joined snapshot must not multiply rows: one row per Bundesland and day.
SELECT bundesland_code, date, count(*) AS duplicates
FROM mart.regional_daily_snapshot
GROUP BY bundesland_code, date
HAVING count(*) > 1;
