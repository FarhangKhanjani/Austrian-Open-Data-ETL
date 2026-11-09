-- Rank 1 must be the cheapest region each day, and the cheapest price can never be
-- above the median of the same list.
SELECT snapshot_date, bundesland_code, fuel_type, price_rank,
       premium_vs_cheapest_region_eur, cheapest_price_eur, median_listed_price_eur
FROM mart.fuel_price_daily
WHERE (price_rank = 1 AND premium_vs_cheapest_region_eur <> 0)
   OR premium_vs_cheapest_region_eur < 0
   OR cheapest_price_eur > median_listed_price_eur;
