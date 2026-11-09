-- A snapshot is replaced as a whole, so all rows of one day, Bundesland and fuel type
-- must come from a single response. Rows from two responses would mean the
-- delete-and-insert did not happen.
SELECT snapshot_date, bundesland_code, fuel_type, count(DISTINCT raw_id) AS responses
FROM staging.fuel_price
GROUP BY snapshot_date, bundesland_code, fuel_type
HAVING count(DISTINCT raw_id) > 1;
