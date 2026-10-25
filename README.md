# Austria Open Data ETL

Daily ETL pipeline that loads Austrian open data into a layered PostgreSQL warehouse
(raw, staging, mart) and feeds a Power BI dashboard.

**Status:** work in progress (Milestone 1 of 8: repository and environment).

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
uv run python -m pipeline.run --check-db
```

## Run tests and linting

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```
