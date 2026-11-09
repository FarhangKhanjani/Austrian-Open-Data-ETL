-- One row per Bundesland and day.
SELECT bundesland_code, obs_date, count(*) AS duplicates
FROM mart.weather_daily_bundesland
GROUP BY bundesland_code, obs_date
HAVING count(*) > 1;
