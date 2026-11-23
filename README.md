# Austria Open Data ETL

Daily ETL pipeline that loads Austrian open data into a layered PostgreSQL warehouse
(raw, staging, mart) and feeds a Power BI dashboard.

**Status:** work in progress (Milestone 6 of 8: daily scheduled job). See [docs/warehouse_design.md](docs/warehouse_design.md), [docs/data_dictionary.md](docs/data_dictionary.md) and [docs/data_quality.md](docs/data_quality.md).

## Data sources

| Source | Content | License |
|---|---|---|
| [GeoSphere Austria Data Hub](https://data.hub.geosphere.at/dataset/klima-v2-1d), `klima-v2-1d` | Daily station weather (temperature, precipitation, sunshine) | CC BY 4.0 |
| [E-Control Spritpreisrechner](https://www.spritpreisrechner.at) | Daily snapshot of the cheapest fuel prices per Bundesland | No explicit license published; used with attribution and minimal request volume |
| [Statistik Austria OGD](https://data.statistik.gv.at) | Population per Bundesland | CC BY 4.0 |

## Run locally

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and Docker Desktop.

```bash
cp .env.example .env
docker compose up -d
uv sync
uv run python -m pipeline.run
```

The last command creates the schema if needed and runs the daily job: weather for the
last 7 days, today's fuel price snapshot, and population if a new year is published.

Load weather history (fuel prices have no history endpoint, so they cannot be backfilled):

```bash
uv run python -m pipeline.run --mode backfill --start 2024-01-01 --end 2026-10-24
```

Exit code 0 means every source succeeded; 1 means at least one source failed (the run
is recorded as `partial` or `failed` in `ops.pipeline_run`).

## Daily job

The workflow `.github/workflows/daily-etl.yml` runs every day at 11:30 UTC on GitHub
Actions. GitHub's runners are temporary, so the production warehouse is a hosted
Postgres (Neon free plan); the local Docker Postgres is only for development and tests.
The job builds the same Docker image that CI checks and runs it with the connection
string from the `DATABASE_URL` repository secret. Each run writes a summary (rows
loaded and rejected per source, data tests, freshness) to its GitHub Actions page.
See [docs/runbook.md](docs/runbook.md) for what to do when it fails.

## Run in a container

The same image runs locally and in the scheduled job. It is configured only through
environment variables and never contains `.env`.

```bash
docker build --tag austria-etl .
docker run --rm --env-file .env --network host austria-etl
```

Arguments after the image name go to `pipeline.run`, for example `--sql-tests` or
`--mode backfill --start 2024-01-01 --end 2024-12-31`. On Windows and macOS, Docker
Desktop does not support `--network host` for reaching a database on your machine;
use `host.docker.internal` instead of `localhost` in `DATABASE_URL`.

## Run tests and linting

Database tests use a separate `<database>_test` database on the Docker Postgres and
are skipped when it is not running locally (in CI they fail instead, so they always
run there). HTTP calls are always mocked.

CI (`.github/workflows/ci.yml`) runs on every push and pull request: ruff, the full
test suite with coverage against a Postgres service container, then builds the Docker
image and runs the schema and SQL data tests from inside it.

```bash
uv run pytest
uv run python -m pipeline.run --sql-tests
uv run ruff check .
uv run ruff format --check .
```
