-- Daily weather per Bundesland with rolling averages, day-over-day change and an
-- anomaly flag.
--
-- Questions it answers: How warm was it in each Bundesland, how does today compare
-- with the past week and with yesterday, and was a day unusually warm or cold?
DROP VIEW IF EXISTS mart.weather_daily_bundesland CASCADE;

CREATE VIEW mart.weather_daily_bundesland AS
WITH station_days AS (
    -- Stations above 1,500 m are excluded: Tirol and Salzburg have many mountain
    -- stations, which would make those regions look colder than where people live.
    SELECT
        s.bundesland_code,
        w.obs_date,
        w.temp_mean_c,
        w.temp_max_c,
        w.temp_min_c,
        w.precip_mm,
        w.sunshine_h
    FROM staging.weather_daily AS w
    JOIN staging.weather_station AS s
        ON s.station_id = w.station_id
    WHERE s.altitude_m < 1500
),

regional AS (
    -- Median, not mean, so one faulty station cannot move a whole region.
    SELECT
        bundesland_code,
        obs_date,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY temp_mean_c::float8) AS temp_mean_c,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY temp_max_c::float8) AS temp_max_c,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY temp_min_c::float8) AS temp_min_c,
        avg(precip_mm)::float8 AS precip_mm,
        avg(sunshine_h)::float8 AS sunshine_h,
        count(temp_mean_c) AS stations_reporting
    FROM station_days
    GROUP BY bundesland_code, obs_date
),

windowed AS (
    SELECT
        r.*,
        LAG(obs_date) OVER by_day AS prev_obs_date,
        LAG(temp_mean_c) OVER by_day AS prev_temp_mean_c,
        avg(temp_mean_c) OVER last_7_days AS temp_mean_7d_avg,
        sum(precip_mm) OVER last_7_days AS precip_7d_sum,
        avg(temp_mean_c) OVER previous_30_days AS temp_baseline_30d_avg,
        stddev_samp(temp_mean_c) OVER previous_30_days AS temp_baseline_30d_stddev
    FROM regional AS r
    -- RANGE with a date interval (not ROWS) keeps the windows correct when a day is
    -- missing: "last 7 days" stays 7 calendar days instead of 7 rows.
    WINDOW
        by_day AS (PARTITION BY bundesland_code ORDER BY obs_date),
        last_7_days AS (
            PARTITION BY bundesland_code ORDER BY obs_date
            RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW
        ),
        previous_30_days AS (
            PARTITION BY bundesland_code ORDER BY obs_date
            RANGE BETWEEN INTERVAL '30 days' PRECEDING AND INTERVAL '1 day' PRECEDING
        )
)

SELECT
    bundesland_code,
    obs_date,
    round(temp_mean_c::numeric, 1) AS temp_mean_c,
    round(temp_max_c::numeric, 1) AS temp_max_c,
    round(temp_min_c::numeric, 1) AS temp_min_c,
    round(precip_mm::numeric, 1) AS precip_mm,
    round(sunshine_h::numeric, 1) AS sunshine_h,
    stations_reporting,
    round(temp_mean_7d_avg::numeric, 1) AS temp_mean_7d_avg,
    round(precip_7d_sum::numeric, 1) AS precip_7d_sum,
    -- Only a real day-over-day change if yesterday exists; otherwise NULL, not a
    -- misleading change against an older day.
    CASE
        WHEN prev_obs_date = obs_date - 1
        THEN round((temp_mean_c - prev_temp_mean_c)::numeric, 1)
    END AS temp_change_vs_prev_day,
    round(temp_baseline_30d_avg::numeric, 1) AS temp_baseline_30d_avg,
    CASE
        WHEN temp_baseline_30d_stddev > 0
        THEN round(((temp_mean_c - temp_baseline_30d_avg) / temp_baseline_30d_stddev)::numeric, 2)
    END AS temp_z_score,
    -- More than two standard deviations from the previous 30 days.
    coalesce(
        abs(temp_mean_c - temp_baseline_30d_avg) > 2 * temp_baseline_30d_stddev,
        false
    ) AS is_temp_anomaly
FROM windowed;
