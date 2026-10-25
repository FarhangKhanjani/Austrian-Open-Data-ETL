# Warehouse design and trade-offs

## Why three layers (raw, staging, mart)

- **raw** keeps every response exactly as received. If a transformation bug is found,
  staging can be rebuilt from raw without calling the APIs again. That matters most for
  E-Control, which has no history endpoint: a missed snapshot can never be fetched
  again, but a badly parsed one can always be re-parsed.
- **staging** is the typed, validated version with real keys. This is where
  idempotency lives.
- **mart** is shaped for questions, not for loading. Power BI only reads this layer,
  so table changes underneath do not break the dashboard as long as the views keep
  their columns.
- **ops** is kept apart so pipeline bookkeeping never mixes with business data.

The cost is storage and some duplication. At this data volume (a few MB per month,
plus one 18 MB population file per year) that cost is negligible.

## Why raw is append-only and stores text

- Each fetch is its own row, even if the body is identical to the previous one. For a
  snapshot source the fetch time is part of the meaning, so deduplicating bodies would
  lose it.
- `content_hash` (SHA-256) still detects "unchanged since last fetch", so the pipeline
  can skip work.
- The body is `text`, not `jsonb`, because `jsonb` reorders keys and strips whitespace,
  and Statistik Austria delivers CSV.

## Two idempotency strategies

| Data | Strategy | Why |
|---|---|---|
| Weather (`staging.weather_daily`) | `INSERT ... ON CONFLICT (station_id, obs_date) DO UPDATE` | Each row has a natural key. Re-fetching a day replaces it with the latest, possibly revised value. |
| Fuel (`staging.fuel_price`) | Delete and insert one `(snapshot_date, bundesland, fuel_type)` slice in one transaction | The snapshot is a list. If a station drops off the list in a second run on the same day, a row upsert would leave a stale row behind. |
| Population, stations, Bundesland | `ON CONFLICT DO UPDATE` | Natural keys, rarely change. |

Either way, running the pipeline twice gives the same result as running it once.

## Constraints in two places

Python validation (Milestone 3) is the main gate and sends bad rows to
`ops.quarantine` with a reason. The database `CHECK` constraints are deliberately
looser sanity bounds (for example temperature -60 to 50 degrees C). They are the last line of
defence if a validation rule is missing. A load that hits one fails loudly instead of
storing garbage.

## Indexes

Only where a known query needs one:

- Primary keys on every staging table (uniqueness and upsert targets).
- `weather_daily (obs_date)`: the PK starts with `station_id`, but mart views
  aggregate all stations over a date range.
- `api_response (source, endpoint, fetched_at DESC)`: "latest response" lookups.
- `quarantine (run_id)`: per-run rejected counts for the job summary.

`pipeline_run` gets no extra index: it grows by one row per day.

## Schema management

`python -m pipeline.run --init-db` runs `sql/schema/*.sql` in file order inside one
transaction. Every statement is re-runnable (`IF NOT EXISTS`, `ON CONFLICT`), and a test
enforces that.

Limitation: this handles adding objects, not altering or dropping them. For a project
this size that is enough. A team project would use a migration tool such as Alembic,
Flyway or dbt.

## Known limitations

- E-Control fuel history starts on the first day the pipeline ran.
- E-Control lists only the cheapest stations, so fuel metrics are "cheapest available
  price".
- GeoSphere daily values are revised for up to 6 months; the daily run re-fetches a
  trailing window, and `min_quality_flag` shows how far a value has been checked.
- Precipitation is measured from 06:01 UTC to 06:00 UTC the next day, not midnight to
  midnight.
