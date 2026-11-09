-- How current each dataset is, and how the last run of each source went.
-- Feeds the "data freshness" indicator on the dashboard.
DROP VIEW IF EXISTS mart.data_freshness CASCADE;

CREATE VIEW mart.data_freshness AS
WITH latest_data AS (
    SELECT 'geosphere' AS source, 'Weather' AS dataset,
           max(obs_date) AS latest_data_date, max(updated_at) AS last_loaded_at
    FROM staging.weather_daily
    UNION ALL
    SELECT 'econtrol', 'Fuel prices', max(snapshot_date), max(loaded_at)
    FROM staging.fuel_price
    UNION ALL
    SELECT 'statistik_austria', 'Population', max(reference_date), max(loaded_at)
    FROM staging.population
),

last_source_run AS (
    SELECT
        s.source,
        s.status,
        r.finished_at,
        ROW_NUMBER() OVER (PARTITION BY s.source ORDER BY r.run_id DESC) AS recency_rank
    FROM ops.pipeline_run_source AS s
    JOIN ops.pipeline_run AS r
        ON r.run_id = s.run_id
)

SELECT
    l.source,
    l.dataset,
    l.latest_data_date,
    l.last_loaded_at,
    (now() AT TIME ZONE 'Europe/Vienna')::date - l.latest_data_date AS days_behind,
    run.status AS last_run_status,
    run.finished_at AS last_run_finished_at
FROM latest_data AS l
LEFT JOIN last_source_run AS run
    ON run.source = l.source
   AND run.recency_rank = 1;
