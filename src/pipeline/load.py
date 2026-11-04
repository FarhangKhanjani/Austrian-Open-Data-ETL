"""Writes to PostgreSQL: raw responses, idempotent staging loads, quarantine, run bookkeeping.

Every staging load can run any number of times with the same result:

* weather, stations, population: INSERT ... ON CONFLICT DO UPDATE on the natural key.
  The update only fires when a value actually changed, so updated_at means something.
* fuel prices: the snapshot for one (date, Bundesland, fuel type) is replaced as a whole,
  because a station that drops off the list must not linger from an earlier run.

Functions here never commit; the caller decides the transaction boundaries.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from functools import partial

import psycopg
from psycopg.types.json import Jsonb

from pipeline.extract import FetchedResponse
from pipeline.validate import (
    FuelPriceRow,
    PopulationRow,
    Rejected,
    WeatherDailyRow,
    WeatherStationRow,
)

# Records in quarantine can hold dates and datetimes; store them as ISO strings.
_to_json = partial(json.dumps, default=str, ensure_ascii=False)


@dataclass(frozen=True)
class SourceResult:
    """Outcome of one source in one run, mirrored into ops.pipeline_run_source."""

    source: str
    status: str
    rows_read: int = 0
    rows_loaded: int = 0
    rows_rejected: int = 0
    error_message: str | None = None


def _returned_id(row: tuple[object, ...] | None) -> int:
    """Unpack the id from an INSERT ... RETURNING row."""
    if row is None:
        raise RuntimeError("INSERT ... RETURNING returned no row")
    return int(row[0])


def start_run(
    conn: psycopg.Connection,
    mode: str,
    window_start: date | None,
    window_end: date | None,
    triggered_by: str,
    git_sha: str | None,
) -> int:
    """Insert a 'running' row and return its run_id."""
    row = conn.execute(
        """
        INSERT INTO ops.pipeline_run (mode, window_start, window_end, triggered_by, git_sha)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING run_id
        """,
        (mode, window_start, window_end, triggered_by, git_sha),
    ).fetchone()
    return _returned_id(row)


def record_source_result(conn: psycopg.Connection, run_id: int, result: SourceResult) -> None:
    conn.execute(
        """
        INSERT INTO ops.pipeline_run_source
            (run_id, source, status, rows_read, rows_loaded, rows_rejected, error_message)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id, source) DO UPDATE
        SET status = EXCLUDED.status,
            rows_read = EXCLUDED.rows_read,
            rows_loaded = EXCLUDED.rows_loaded,
            rows_rejected = EXCLUDED.rows_rejected,
            error_message = EXCLUDED.error_message
        """,
        (
            run_id,
            result.source,
            result.status,
            result.rows_read,
            result.rows_loaded,
            result.rows_rejected,
            result.error_message,
        ),
    )


def finish_run(
    conn: psycopg.Connection, run_id: int, status: str, error_message: str | None = None
) -> None:
    """Close the run and roll the per-source counters up into the run row."""
    conn.execute(
        """
        UPDATE ops.pipeline_run AS r
        SET finished_at   = now(),
            status        = %s,
            error_message = %s,
            rows_read     = s.rows_read,
            rows_loaded   = s.rows_loaded,
            rows_rejected = s.rows_rejected
        FROM (
            SELECT coalesce(sum(rows_read), 0)     AS rows_read,
                   coalesce(sum(rows_loaded), 0)   AS rows_loaded,
                   coalesce(sum(rows_rejected), 0) AS rows_rejected
            FROM ops.pipeline_run_source
            WHERE run_id = %s
        ) AS s
        WHERE r.run_id = %s
        """,
        (status, error_message, run_id, run_id),
    )


def insert_raw(conn: psycopg.Connection, run_id: int, response: FetchedResponse) -> int:
    """Store one response unchanged and return its raw_id."""
    row = conn.execute(
        """
        INSERT INTO raw.api_response
            (run_id, source, endpoint, source_url, request_params, http_status,
             content_type, body, content_hash, fetched_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING raw_id
        """,
        (
            run_id,
            response.source,
            response.endpoint,
            response.url,
            Jsonb(response.params),
            response.status_code,
            response.content_type,
            response.body,
            response.content_hash,
            response.fetched_at,
        ),
    ).fetchone()
    return _returned_id(row)


def insert_quarantine(
    conn: psycopg.Connection,
    run_id: int,
    raw_id: int | None,
    source: str,
    target_table: str,
    rejected: Sequence[Rejected],
) -> None:
    if not rejected:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO ops.quarantine (run_id, raw_id, source, target_table, record, reasons)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [
                (run_id, raw_id, source, target_table, Jsonb(r.record, dumps=_to_json), r.reasons)
                for r in rejected
            ],
        )


def known_station_ids(conn: psycopg.Connection) -> set[int]:
    return {row[0] for row in conn.execute("SELECT station_id FROM staging.weather_station")}


def upsert_stations(conn: psycopg.Connection, rows: Sequence[WeatherStationRow]) -> int:
    """Insert or update station metadata. Returns the number of rows inserted or changed."""
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO staging.weather_station
                (station_id, station_name, bundesland_code, latitude, longitude, altitude_m,
                 valid_from, valid_to, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (station_id) DO UPDATE
            SET station_name    = EXCLUDED.station_name,
                bundesland_code = EXCLUDED.bundesland_code,
                latitude        = EXCLUDED.latitude,
                longitude       = EXCLUDED.longitude,
                altitude_m      = EXCLUDED.altitude_m,
                valid_from      = EXCLUDED.valid_from,
                valid_to        = EXCLUDED.valid_to,
                is_active       = EXCLUDED.is_active,
                updated_at      = now()
            WHERE (weather_station.station_name, weather_station.bundesland_code,
                   weather_station.latitude, weather_station.longitude,
                   weather_station.altitude_m, weather_station.valid_from,
                   weather_station.valid_to, weather_station.is_active)
                  IS DISTINCT FROM
                  (EXCLUDED.station_name, EXCLUDED.bundesland_code, EXCLUDED.latitude,
                   EXCLUDED.longitude, EXCLUDED.altitude_m, EXCLUDED.valid_from,
                   EXCLUDED.valid_to, EXCLUDED.is_active)
            """,
            [
                (
                    r.station_id,
                    r.station_name,
                    r.bundesland_code,
                    r.latitude,
                    r.longitude,
                    r.altitude_m,
                    r.valid_from,
                    r.valid_to,
                    r.is_active,
                )
                for r in rows
            ],
        )
        return max(cur.rowcount, 0)


def upsert_weather(conn: psycopg.Connection, rows: Sequence[WeatherDailyRow], raw_id: int) -> int:
    """Insert new days, update revised ones. Returns the number of rows inserted or changed.

    raw_id is only updated together with the values, so it always points at the
    response the current values came from.
    """
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO staging.weather_daily
                (station_id, obs_date, temp_mean_c, temp_max_c, temp_min_c, precip_mm,
                 sunshine_h, min_quality_flag, raw_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (station_id, obs_date) DO UPDATE
            SET temp_mean_c      = EXCLUDED.temp_mean_c,
                temp_max_c       = EXCLUDED.temp_max_c,
                temp_min_c       = EXCLUDED.temp_min_c,
                precip_mm        = EXCLUDED.precip_mm,
                sunshine_h       = EXCLUDED.sunshine_h,
                min_quality_flag = EXCLUDED.min_quality_flag,
                raw_id           = EXCLUDED.raw_id,
                updated_at       = now()
            WHERE (weather_daily.temp_mean_c, weather_daily.temp_max_c,
                   weather_daily.temp_min_c, weather_daily.precip_mm,
                   weather_daily.sunshine_h, weather_daily.min_quality_flag)
                  IS DISTINCT FROM
                  (EXCLUDED.temp_mean_c, EXCLUDED.temp_max_c, EXCLUDED.temp_min_c,
                   EXCLUDED.precip_mm, EXCLUDED.sunshine_h, EXCLUDED.min_quality_flag)
            """,
            [
                (
                    r.station_id,
                    r.obs_date,
                    r.temp_mean_c,
                    r.temp_max_c,
                    r.temp_min_c,
                    r.precip_mm,
                    r.sunshine_h,
                    r.min_quality_flag,
                    raw_id,
                )
                for r in rows
            ],
        )
        return max(cur.rowcount, 0)


def replace_fuel_snapshot(
    conn: psycopg.Connection,
    snapshot_date: date,
    bundesland_code: int,
    fuel_type: str,
    rows: Sequence[FuelPriceRow],
    raw_id: int,
) -> int:
    """Replace the snapshot for one date, Bundesland and fuel type. Returns rows inserted."""
    conn.execute(
        """
        DELETE FROM staging.fuel_price
        WHERE snapshot_date = %s AND bundesland_code = %s AND fuel_type = %s
        """,
        (snapshot_date, bundesland_code, fuel_type),
    )
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO staging.fuel_price
                (snapshot_date, bundesland_code, fuel_type, station_id, station_name,
                 postal_code, city, latitude, longitude, price_eur, list_position,
                 fetched_at, raw_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    r.snapshot_date,
                    r.bundesland_code,
                    r.fuel_type,
                    r.station_id,
                    r.station_name,
                    r.postal_code,
                    r.city,
                    r.latitude,
                    r.longitude,
                    r.price_eur,
                    r.list_position,
                    r.fetched_at,
                    raw_id,
                )
                for r in rows
            ],
        )
    return len(rows)


def population_years(conn: psycopg.Connection) -> set[int]:
    """Years for which all nine Bundesländer already have a population figure."""
    return {
        int(row[0])
        for row in conn.execute(
            """
            SELECT EXTRACT(YEAR FROM reference_date)
            FROM staging.population
            GROUP BY reference_date
            HAVING count(*) = 9
            """
        )
    }


def upsert_population(conn: psycopg.Connection, rows: Sequence[PopulationRow], raw_id: int) -> int:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO staging.population (bundesland_code, reference_date, population, raw_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (bundesland_code, reference_date) DO UPDATE
            SET population = EXCLUDED.population,
                raw_id     = EXCLUDED.raw_id
            WHERE population.population IS DISTINCT FROM EXCLUDED.population
            """,
            [(r.bundesland_code, r.reference_date, r.population, raw_id) for r in rows],
        )
        return max(cur.rowcount, 0)
