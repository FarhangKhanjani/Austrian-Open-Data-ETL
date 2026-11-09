-- One row per Bundesland with its most recent population figure.
--
-- Views are dropped and recreated on every schema apply (instead of CREATE OR REPLACE)
-- so a view can gain, lose or reorder columns. CASCADE also drops views built on this
-- one; they are recreated by the later, higher-numbered files in the same transaction.
DROP VIEW IF EXISTS mart.dim_bundesland CASCADE;

CREATE VIEW mart.dim_bundesland AS
WITH ranked_population AS (
    SELECT
        bundesland_code,
        reference_date,
        population,
        ROW_NUMBER() OVER (
            PARTITION BY bundesland_code
            ORDER BY reference_date DESC
        ) AS recency_rank
    FROM staging.population
)
SELECT
    b.bundesland_code,
    b.name_de,
    b.name_en,
    b.iso_3166_2,
    p.population,
    p.reference_date AS population_reference_date
FROM staging.bundesland AS b
LEFT JOIN ranked_population AS p
    ON p.bundesland_code = b.bundesland_code
   AND p.recency_rank = 1;
