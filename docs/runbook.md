# Runbook: daily-etl

What runs, how to see whether it worked, and what to do when it did not.

## What runs

| | |
|---|---|
| Workflow | `.github/workflows/daily-etl.yml` |
| When | Every day at 11:30 UTC (12:30 Vienna in winter, 13:30 in summer), plus manual runs |
| What | Builds the Docker image, then `python -m pipeline.run` against the production database |
| Database | Neon Postgres (free plan, Frankfurt), connection string in the `DATABASE_URL` repository secret |
| Sources | GeoSphere weather (last 7 days, re-fetched for revisions), E-Control fuel snapshot, Statistik Austria population (only when a new year appears) |

### Why 11:30 UTC

Austrian fuel stations may raise prices only at 12:00 Vienna time, so a snapshot taken
after that is stable for the rest of the day. Cron in GitHub Actions is always UTC and
ignores daylight saving time: 11:30 UTC is 12:30 in winter (CET) and 13:30 in summer
(CEST), after 12:00 in both cases. Yesterday's weather is published long before.

## Is it healthy?

1. **GitHub:** Actions tab, workflow `daily-etl`. Each run's Summary page shows rows
   read, loaded and rejected per source, SQL data test results, and data freshness.
2. **Database:** `SELECT * FROM mart.data_freshness;` shows the newest date per dataset
   and how the last run of each source ended. `days_behind` for weather is normally 1.
3. **Dashboard:** the freshness card reads the same view.

GitHub emails the person who last changed the workflow's schedule when a scheduled run
fails.

## Exit codes and statuses

| Status in `ops.pipeline_run` | Exit code | Meaning |
|---|---|---|
| success | 0 | All sources loaded, all SQL data tests passed |
| partial | 1 | At least one source failed or a data test failed; the others loaded |
| failed | 1 | Every source failed |
| running (with no finished_at) | | The process was killed mid-run (timeout or crash) |
| | 2 | Configuration error, for example a missing or malformed `DATABASE_URL` |

## When the job fails

Start with the run's Summary page, then the log of the "Run pipeline" step. Then:

```sql
-- The latest runs and what went wrong
SELECT run_id, started_at, status, rows_loaded, rows_rejected, error_message
FROM ops.pipeline_run ORDER BY run_id DESC LIMIT 5;

-- Per source
SELECT * FROM ops.pipeline_run_source
WHERE run_id = (SELECT max(run_id) FROM ops.pipeline_run);

-- Rejected rows and why
SELECT target_table, reasons, record
FROM ops.quarantine
WHERE run_id = (SELECT max(run_id) FROM ops.pipeline_run)
LIMIT 20;
```

| Symptom | Likely cause | Action |
|---|---|---|
| "Repository secret DATABASE_URL is not set" | Secret missing or renamed | Add it under Settings, Secrets and variables, Actions |
| Exit code 2 | Malformed `DATABASE_URL` | Check it starts with `postgresql://` and ends with `?sslmode=require` |
| Connection timeout or "endpoint" error | Neon outage, or the compute is waking from scale-to-zero | Re-run the workflow; check status.neon.tech |
| Neon "project size limit" error on insert | Free plan storage full (1 GB) | Check `pg_database_size`; delete old `raw.api_response` rows or upgrade |
| `geosphere` failed: "newest data is from ..." | GeoSphere has not published recent days | Usually resolves itself; the next run re-fetches the last 7 days anyway |
| `econtrol` failed for some regions | E-Control API errors or changed format | Fuel for that day is lost for those regions (no history API). Check the response in `raw.api_response` |
| `econtrol` "no valid prices in response" | Empty or unusable list | Existing snapshot was kept; inspect the raw body |
| Many rows in quarantine | Source changed a field or a unit | Read `reasons`, fix `transform.py` or `validate.py`, then re-run |
| "data tests failed: ..." | A rule in `sql/tests/` broke | Run the named test file by hand to see the offending rows |
| Run stuck in `running` | Job killed by the 30-minute timeout | Look at the step log for where it hung; re-run |

### Re-running safely

Every load is idempotent, so re-running is always safe:

- **Today:** Actions, `daily-etl`, Run workflow, mode `daily`.
- **Weather for a past range:** Run workflow, mode `backfill`, with start and end dates.
  Fuel prices cannot be backfilled; E-Control has no history endpoint.
- **Locally against production** (rarely needed): put the production `DATABASE_URL` in
  `.env` and run `uv run python -m pipeline.run`. Remove it afterwards.

## Scheduled workflows can stop on their own

- **Delays:** GitHub starts scheduled runs on a best-effort basis; under load they can
  start late or, rarely, be skipped. The 7-day weather window makes a skipped day
  harmless for weather, but that day's fuel snapshot is missing.
- **Inactivity:** in a public repository, GitHub disables scheduled workflows after 60
  days without repository activity, and shows a banner in the Actions tab. Fix: open
  Actions, select `daily-etl`, click "Enable workflow". Any commit within the 60 days
  also resets the timer. The dashboard's freshness indicator shows if this happened.

## Rotating the database password

1. Reset the role password in the Neon console.
2. Copy the new connection string into the `DATABASE_URL` repository secret.
3. Update Power BI's stored credentials (File, Options and settings, Data source settings).
4. Run the workflow manually once to confirm.
