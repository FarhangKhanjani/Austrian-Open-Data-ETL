# Data quality rules

Every record goes through `pipeline.validate` before it reaches a staging table.
A record that fails any rule goes to `ops.quarantine` with all of its reasons, and the
rest of the batch still loads. Nothing is dropped without a trace.

## Row rules (pydantic models)

| Table | Rule | Why |
|---|---|---|
| all | Unknown or misspelled fields are rejected (`extra="forbid"`) | Catches a transform bug instead of silently ignoring a column |
| weather_station | Bundesland must map to code 1 to 9 | GeoSphere also lists stations outside Austria |
| weather_station | Latitude 46.3 to 49.1, longitude 9.4 to 17.2 | Bounding box of Austria |
| weather_station | Altitude 100 to 3,800 m | Lowest and highest points in Austria, with margin |
| weather_daily | Temperatures -50 to 45 degrees C | Austrian records are about -52 and 41 |
| weather_daily | `temp_min_c <= temp_max_c` | Physically impossible otherwise |
| weather_daily | Precipitation 0 to 300 mm per day | Above the Austrian daily record |
| weather_daily | Sunshine 0 to 24 h | A day has 24 hours |
| weather_daily | Station must exist in `weather_station` | Prevents one bad row failing the whole batch on the foreign key |
| fuel_price | Price required, 0.8 to 3.5 EUR per litre | Catches missing prices and typos such as 16.49 |
| fuel_price | Fuel type `DIE` or `SUP` | Only the two requested types |
| population | 100,000 to 5,000,000 per Bundesland | Burgenland is about 300,000, Wien about 2 million |

These ranges are stricter than the database `CHECK` constraints on purpose: validation
is the gate, and the constraints are a backstop.

## Batch rules

| Rule | Behaviour |
|---|---|
| Duplicate key inside one response | First record wins, the others are quarantined |
| Weather freshness (daily mode) | If the newest weather date is more than 3 days old, the source is marked `failed`, so the run ends `partial` and the job exits with code 1 |
| Empty fuel snapshot | The existing snapshot is kept (never replaced by an empty list), and the source is marked `failed` |
| Population completeness | All nine Bundesländer must be present, otherwise nothing is loaded |

## Source conventions handled in transform

| Source value | Meaning | Stored as |
|---|---|---|
| GeoSphere `rr = -1` | No precipitation | `0.0` |
| GeoSphere `rr = 0` | Less than 0.1 mm | `0.0` |
| GeoSphere row with every value empty | Day not published yet, or the station does not measure | Skipped and counted, not quarantined |
| GeoSphere `*_flag` | Quality level per value (0 unchecked, 10 to 12 automatic, 20 to 22 manual) | Lowest flag of the row in `min_quality_flag` |
| E-Control fetch time | UTC timestamp | `snapshot_date` is the Vienna calendar date |

## Inspecting rejected rows

```sql
SELECT q.quarantined_at, q.target_table, q.reasons, q.record
FROM ops.quarantine AS q
ORDER BY q.quarantine_id DESC
LIMIT 20;
```
