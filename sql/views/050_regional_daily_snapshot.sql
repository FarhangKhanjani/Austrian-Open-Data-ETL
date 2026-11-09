-- One row per Bundesland and day, joining weather, fuel prices and population.
-- This is the main table for the dashboard.
--
-- FULL OUTER JOIN on purpose: weather for a day arrives the next morning, while the fuel
-- snapshot is taken the same day. An inner join would hide today's fuel prices until
-- the weather catches up; a left join from either side would hide the other source.
DROP VIEW IF EXISTS mart.regional_daily_snapshot CASCADE;

CREATE VIEW mart.regional_daily_snapshot AS
WITH fuel_wide AS (
    -- Pivot the two fuel types into columns, one row per Bundesland and day.
    SELECT
        bundesland_code,
        snapshot_date,
        max(cheapest_price_eur) FILTER (WHERE fuel_type = 'DIE') AS diesel_cheapest_eur,
        max(price_rank) FILTER (WHERE fuel_type = 'DIE') AS diesel_price_rank,
        max(cheapest_price_eur) FILTER (WHERE fuel_type = 'SUP') AS super95_cheapest_eur,
        max(price_rank) FILTER (WHERE fuel_type = 'SUP') AS super95_price_rank
    FROM mart.fuel_price_daily
    GROUP BY bundesland_code, snapshot_date
),

joined AS (
    SELECT
        coalesce(w.bundesland_code, f.bundesland_code) AS bundesland_code,
        coalesce(w.obs_date, f.snapshot_date) AS date,
        w.temp_mean_c,
        w.temp_mean_7d_avg,
        w.temp_change_vs_prev_day,
        w.is_temp_anomaly,
        w.precip_mm,
        w.sunshine_h,
        f.diesel_cheapest_eur,
        f.diesel_price_rank,
        f.super95_cheapest_eur,
        f.super95_price_rank
    FROM mart.weather_daily_bundesland AS w
    FULL OUTER JOIN fuel_wide AS f
        ON f.bundesland_code = w.bundesland_code
       AND f.snapshot_date = w.obs_date
)

SELECT
    j.date,
    j.bundesland_code,
    d.name_de AS bundesland_name_de,
    d.name_en AS bundesland_name_en,
    d.iso_3166_2,
    d.population,
    j.temp_mean_c,
    j.temp_mean_7d_avg,
    j.temp_change_vs_prev_day,
    j.is_temp_anomaly,
    j.precip_mm,
    j.sunshine_h,
    j.diesel_cheapest_eur,
    j.diesel_price_rank,
    j.super95_cheapest_eur,
    j.super95_price_rank,
    j.temp_mean_c IS NOT NULL AS has_weather,
    j.diesel_cheapest_eur IS NOT NULL OR j.super95_cheapest_eur IS NOT NULL AS has_fuel
FROM joined AS j
JOIN mart.dim_bundesland AS d
    ON d.bundesland_code = j.bundesland_code;
