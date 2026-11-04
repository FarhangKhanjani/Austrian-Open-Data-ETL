"""One job per source: extract, store raw, transform, validate, load.

Transaction pattern, the same in every job:

1. Fetch, insert into raw.api_response, COMMIT. The raw copy survives even if a later
   step fails, which matters most for E-Control snapshots that can never be re-fetched.
2. Parse, validate, write staging rows and quarantine rows, COMMIT. Either the whole
   batch lands or none of it does.

A job never raises. It returns a SourceResult, so one broken source cannot stop the
others, and the run ends as 'partial' instead of losing everything.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import psycopg

from pipeline import config, extract, load, transform, validate
from pipeline.extract import ExtractError, HttpClient
from pipeline.load import SourceResult

logger = logging.getLogger("pipeline.jobs")


@dataclass(frozen=True)
class JobContext:
    conn: psycopg.Connection
    client: HttpClient
    run_id: int
    today: date


@dataclass
class _Counts:
    read: int = 0
    loaded: int = 0
    rejected: int = 0


def _guarded(source: str, ctx: JobContext, body: Callable[[], SourceResult]) -> SourceResult:
    """Run a job body; turn any exception into a failed SourceResult after rolling back."""
    try:
        return body()
    except Exception as exc:
        ctx.conn.rollback()
        logger.exception("source failed", extra={"ctx": {"source": source}})
        return SourceResult(source=source, status="failed", error_message=str(exc)[:500])


def run_weather(ctx: JobContext, start: date, end: date, check_freshness: bool) -> SourceResult:
    """Refresh station metadata, then load daily weather for [start, end]."""
    return _guarded("geosphere", ctx, lambda: _weather(ctx, start, end, check_freshness))


def _weather(ctx: JobContext, start: date, end: date, check_freshness: bool) -> SourceResult:
    counts = _Counts()
    conn = ctx.conn

    response = extract.fetch_geosphere_stations(ctx.client)
    raw_id = load.insert_raw(conn, ctx.run_id, response)
    conn.commit()

    # Only active stations: they are the ones we request data for. Inactive ones would
    # add noise to quarantine every day without ever producing weather rows.
    station_records = [
        r for r in transform.parse_geosphere_stations(response.body) if r.get("is_active")
    ]
    stations = validate.validate_records(
        station_records, validate.WeatherStationRow, ["station_id"]
    )
    load.upsert_stations(conn, stations.valid)
    load.insert_quarantine(
        conn, ctx.run_id, raw_id, "geosphere", "staging.weather_station", stations.rejected
    )
    conn.commit()
    counts.read += len(station_records)
    counts.loaded += len(stations.valid)
    counts.rejected += len(stations.rejected)

    station_ids = sorted(row.station_id for row in stations.valid)
    known = load.known_station_ids(conn)
    latest: date | None = None
    skipped_empty = 0

    for chunk_start, chunk_end in extract.month_chunks(start, end):
        response = extract.fetch_geosphere_daily(ctx.client, station_ids, chunk_start, chunk_end)
        raw_id = load.insert_raw(conn, ctx.run_id, response)
        conn.commit()

        records, empty = transform.parse_geosphere_daily(response.body)
        result = validate.validate_records(
            records, validate.WeatherDailyRow, ["station_id", "obs_date"]
        )
        validate.reject_unknown_stations(result, known)
        changed = load.upsert_weather(conn, result.valid, raw_id)
        load.insert_quarantine(
            conn, ctx.run_id, raw_id, "geosphere", "staging.weather_daily", result.rejected
        )
        conn.commit()

        skipped_empty += empty
        counts.read += len(records)
        counts.loaded += len(result.valid)
        counts.rejected += len(result.rejected)
        if result.valid:
            chunk_latest = max(row.obs_date for row in result.valid)
            latest = chunk_latest if latest is None else max(latest, chunk_latest)
        logger.info(
            "weather chunk loaded",
            extra={
                "ctx": {
                    "start": chunk_start,
                    "end": chunk_end,
                    "valid": len(result.valid),
                    "changed": changed,
                    "rejected": len(result.rejected),
                    "empty_skipped": empty,
                }
            },
        )

    problem = None
    if check_freshness:
        problem = validate.check_freshness(latest, ctx.today, config.WEATHER_MAX_LAG_DAYS)
    logger.info(
        "weather done",
        extra={"ctx": {"latest_date": latest, "empty_skipped": skipped_empty}},
    )
    return SourceResult(
        source="geosphere",
        status="failed" if problem else "success",
        rows_read=counts.read,
        rows_loaded=counts.loaded,
        rows_rejected=counts.rejected,
        error_message=problem,
    )


def run_fuel(ctx: JobContext) -> SourceResult:
    """Snapshot the cheapest fuel prices for every Bundesland and fuel type."""
    return _guarded("econtrol", ctx, lambda: _fuel(ctx))


def _fuel(ctx: JobContext) -> SourceResult:
    counts = _Counts()
    problems: list[str] = []
    conn = ctx.conn

    for bundesland_code in range(1, 10):
        for fuel_type in config.FUEL_TYPES:
            label = f"Bundesland {bundesland_code} {fuel_type}"
            try:
                response = extract.fetch_econtrol_region(ctx.client, bundesland_code, fuel_type)
            except ExtractError as exc:
                problems.append(f"{label}: {exc}")
                continue
            raw_id = load.insert_raw(conn, ctx.run_id, response)
            conn.commit()

            records = transform.parse_econtrol_region(
                response.body, bundesland_code, fuel_type, response.fetched_at
            )
            result = validate.validate_records(records, validate.FuelPriceRow, ["station_id"])
            load.insert_quarantine(
                conn, ctx.run_id, raw_id, "econtrol", "staging.fuel_price", result.rejected
            )
            counts.read += len(records)
            counts.rejected += len(result.rejected)

            if not result.valid:
                # Never replace a good snapshot with an empty one.
                problems.append(f"{label}: no valid prices in response")
                conn.commit()
                continue

            snapshot_date = result.valid[0].snapshot_date
            counts.loaded += load.replace_fuel_snapshot(
                conn, snapshot_date, bundesland_code, fuel_type, result.valid, raw_id
            )
            conn.commit()

    logger.info(
        "fuel done",
        extra={"ctx": {"loaded": counts.loaded, "rejected": counts.rejected}},
    )
    return SourceResult(
        source="econtrol",
        status="failed" if problems else "success",
        rows_read=counts.read,
        rows_loaded=counts.loaded,
        rows_rejected=counts.rejected,
        error_message="; ".join(problems)[:500] or None,
    )


def run_population(ctx: JobContext) -> SourceResult:
    """Load the newest published population figures if they are not loaded yet."""
    return _guarded("statistik_austria", ctx, lambda: _population(ctx))


def _population(ctx: JobContext) -> SourceResult:
    conn = ctx.conn
    loaded_years = load.population_years(conn)

    # Statistik Austria publishes 1 January figures a few months into the year, so the
    # current year may not exist yet; then fall back to the previous year.
    response = None
    for year in (ctx.today.year, ctx.today.year - 1):
        if year in loaded_years:
            return SourceResult(
                source="statistik_austria",
                status="skipped",
                error_message=f"population for {year} already loaded",
            )
        try:
            response = extract.fetch_population(ctx.client, year)
            break
        except ExtractError as exc:
            if exc.status_code != 404:
                raise
            logger.info("population not published yet", extra={"ctx": {"year": year}})
    if response is None:
        raise ExtractError("no population file found for the current or previous year")

    raw_id = load.insert_raw(conn, ctx.run_id, response)
    conn.commit()

    records = transform.parse_population(response.body)
    result = validate.validate_records(
        records, validate.PopulationRow, ["bundesland_code", "reference_date"]
    )
    problem = validate.check_all_bundeslaender(row.bundesland_code for row in result.valid)
    if problem is None:
        load.upsert_population(conn, result.valid, raw_id)
    load.insert_quarantine(
        conn, ctx.run_id, raw_id, "statistik_austria", "staging.population", result.rejected
    )
    conn.commit()

    return SourceResult(
        source="statistik_austria",
        status="failed" if problem else "success",
        rows_read=len(records),
        rows_loaded=0 if problem else len(result.valid),
        rows_rejected=len(result.rejected),
        error_message=problem,
    )
