-- Monthly weather per Bundesland, compared with the same month one year earlier, and a
-- ranking of the Bundesländer within each month.
--
-- Questions it answers: Was this October warmer or wetter than last October? Which
-- Bundesland was warmest and sunniest each month?
DROP VIEW IF EXISTS mart.weather_monthly_bundesland CASCADE;

CREATE VIEW mart.weather_monthly_bundesland AS
WITH monthly AS (
    SELECT
        bundesland_code,
        date_trunc('month', obs_date)::date AS month_start,
        avg(temp_mean_c) AS temp_mean_c,
        sum(precip_mm) AS precip_mm_total,
        sum(sunshine_h) AS sunshine_h_total,
        count(*) AS days_with_data
    FROM mart.weather_daily_bundesland
    GROUP BY bundesland_code, date_trunc('month', obs_date)
)

SELECT
    m.bundesland_code,
    m.month_start,
    m.days_with_data,
    -- A month still in progress is flagged, so it is not compared as if it were complete.
    m.days_with_data = extract(DAY FROM (m.month_start + INTERVAL '1 month - 1 day'))
        AS is_complete_month,
    round(m.temp_mean_c, 1) AS temp_mean_c,
    round(m.precip_mm_total, 1) AS precip_mm_total,
    round(m.sunshine_h_total, 1) AS sunshine_h_total,
    -- Temperature: a difference in degrees. A percent change of a temperature in
    -- Celsius is meaningless (it flips sign around 0 degrees).
    round(m.temp_mean_c - py.temp_mean_c, 1) AS temp_diff_vs_prev_year_c,
    round(100 * (m.precip_mm_total - py.precip_mm_total) / nullif(py.precip_mm_total, 0), 1)
        AS precip_pct_change_vs_prev_year,
    round(100 * (m.sunshine_h_total - py.sunshine_h_total) / nullif(py.sunshine_h_total, 0), 1)
        AS sunshine_pct_change_vs_prev_year,
    RANK() OVER (PARTITION BY m.month_start ORDER BY m.temp_mean_c DESC) AS warmest_rank,
    RANK() OVER (PARTITION BY m.month_start ORDER BY m.sunshine_h_total DESC)
        AS sunniest_rank
FROM monthly AS m
-- A self-join on "same month, one year earlier" instead of LAG(..., 12): LAG counts
-- rows, so a single missing month would silently compare against the wrong month.
LEFT JOIN monthly AS py
    ON py.bundesland_code = m.bundesland_code
   AND py.month_start = (m.month_start - INTERVAL '1 year')::date;
