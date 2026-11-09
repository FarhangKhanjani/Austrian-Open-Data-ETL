-- Weather is observed, never forecast: no date after today (Vienna time).
SELECT station_id, obs_date
FROM staging.weather_daily
WHERE obs_date > (now() AT TIME ZONE 'Europe/Vienna')::date;
