-- Daily cheapest fuel price per Bundesland and fuel type, with day-over-day change,
-- a 7-day rolling average and a ranking of the Bundesländer.
--
-- Questions it answers: Where is fuel cheapest today? Did prices rise or fall since
-- yesterday? How much more does the most expensive region pay than the cheapest?
--
-- E-Control lists only the cheapest stations, so "cheapest_price_eur" is the cheapest
-- available price and "median_listed_price_eur" is the median of that short list,
-- not a market average.
DROP VIEW IF EXISTS mart.fuel_price_daily CASCADE;

CREATE VIEW mart.fuel_price_daily AS
WITH daily AS (
    SELECT
        snapshot_date,
        bundesland_code,
        fuel_type,
        min(price_eur) AS cheapest_price_eur,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY price_eur::float8) AS median_listed_price_eur,
        count(*) AS stations_listed,
        max(fetched_at) AS fetched_at
    FROM staging.fuel_price
    GROUP BY snapshot_date, bundesland_code, fuel_type
),

windowed AS (
    SELECT
        d.*,
        LAG(snapshot_date) OVER by_day AS prev_snapshot_date,
        LAG(cheapest_price_eur) OVER by_day AS prev_cheapest_price_eur,
        avg(cheapest_price_eur) OVER (
            PARTITION BY bundesland_code, fuel_type
            ORDER BY snapshot_date
            RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW
        ) AS cheapest_7d_avg,
        min(cheapest_price_eur) OVER same_day_same_fuel AS cheapest_in_austria_eur
    FROM daily AS d
    WINDOW
        by_day AS (PARTITION BY bundesland_code, fuel_type ORDER BY snapshot_date),
        same_day_same_fuel AS (PARTITION BY snapshot_date, fuel_type)
)

SELECT
    snapshot_date,
    bundesland_code,
    fuel_type,
    CASE fuel_type WHEN 'DIE' THEN 'Diesel' WHEN 'SUP' THEN 'Super 95' END AS fuel_name,
    cheapest_price_eur,
    round(median_listed_price_eur::numeric, 3) AS median_listed_price_eur,
    stations_listed,
    round(cheapest_7d_avg, 3) AS cheapest_7d_avg,
    CASE
        WHEN prev_snapshot_date = snapshot_date - 1
        THEN cheapest_price_eur - prev_cheapest_price_eur
    END AS change_vs_prev_day_eur,
    CASE
        WHEN prev_snapshot_date = snapshot_date - 1
        THEN round(100 * (cheapest_price_eur - prev_cheapest_price_eur) / prev_cheapest_price_eur, 2)
    END AS pct_change_vs_prev_day,
    -- 1 = cheapest Bundesland that day. RANK gives ties the same rank.
    RANK() OVER (PARTITION BY snapshot_date, fuel_type ORDER BY cheapest_price_eur)
        AS price_rank,
    cheapest_price_eur - cheapest_in_austria_eur AS premium_vs_cheapest_region_eur,
    fetched_at
FROM windowed;
