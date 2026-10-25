"""Schema tests against a real PostgreSQL (docker compose up -d)."""

from datetime import UTC, date, datetime

import psycopg
import pytest

from pipeline import db

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    ("ops", "pipeline_run"),
    ("ops", "pipeline_run_source"),
    ("ops", "quarantine"),
    ("raw", "api_response"),
    ("staging", "bundesland"),
    ("staging", "weather_station"),
    ("staging", "weather_daily"),
    ("staging", "fuel_price"),
    ("staging", "population"),
}


def _insert_raw(conn: psycopg.Connection) -> int:
    run_id = conn.execute(
        "INSERT INTO ops.pipeline_run (mode) VALUES ('daily') RETURNING run_id"
    ).fetchone()[0]
    return conn.execute(
        """
        INSERT INTO raw.api_response
            (run_id, source, endpoint, source_url, http_status, body, content_hash, fetched_at)
        VALUES (%s, 'geosphere', 'klima-v2-1d', 'https://example.test', 200, '{}', %s, %s)
        RETURNING raw_id
        """,
        (run_id, "0" * 64, datetime.now(UTC)),
    ).fetchone()[0]


def _insert_station(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        INSERT INTO staging.weather_station
            (station_id, station_name, bundesland_code, latitude, longitude, is_active)
        VALUES (3202, 'Linz Stadt', 4, 48.29639, 14.28528, true)
        """
    )


def test_apply_schema_twice_is_idempotent(pg_conn: psycopg.Connection) -> None:
    db.apply_schema(pg_conn)
    db.apply_schema(pg_conn)

    tables = set(
        pg_conn.execute(
            """
            SELECT table_schema, table_name FROM information_schema.tables
            WHERE table_schema IN ('ops', 'raw', 'staging')
            """
        ).fetchall()
    )
    assert EXPECTED_TABLES <= tables
    assert pg_conn.execute("SELECT count(*) FROM staging.bundesland").fetchone()[0] == 9


def test_weather_row_with_min_above_max_is_rejected(pg_conn: psycopg.Connection) -> None:
    db.apply_schema(pg_conn)
    raw_id = _insert_raw(pg_conn)
    _insert_station(pg_conn)

    with pytest.raises(psycopg.errors.CheckViolation, match="weather_daily_temp_order_chk"):
        pg_conn.execute(
            """
            INSERT INTO staging.weather_daily (station_id, obs_date, temp_min_c, temp_max_c, raw_id)
            VALUES (3202, %s, 12.0, 5.0, %s)
            """,
            (date(2026, 10, 24), raw_id),
        )


def test_weather_primary_key_blocks_duplicates(pg_conn: psycopg.Connection) -> None:
    db.apply_schema(pg_conn)
    raw_id = _insert_raw(pg_conn)
    _insert_station(pg_conn)
    insert = """
        INSERT INTO staging.weather_daily (station_id, obs_date, temp_mean_c, raw_id)
        VALUES (3202, %s, 8.7, %s)
    """
    pg_conn.execute(insert, (date(2026, 10, 24), raw_id))

    with pytest.raises(psycopg.errors.UniqueViolation):
        pg_conn.execute(insert, (date(2026, 10, 24), raw_id))


def test_quarantine_requires_a_reason(pg_conn: psycopg.Connection) -> None:
    db.apply_schema(pg_conn)
    raw_id = _insert_raw(pg_conn)

    with pytest.raises(psycopg.errors.CheckViolation, match="quarantine_reasons_chk"):
        pg_conn.execute(
            """
            INSERT INTO ops.quarantine (run_id, raw_id, source, target_table, record, reasons)
            SELECT run_id, raw_id, 'geosphere', 'staging.weather_daily', '{}', '{}'
            FROM raw.api_response WHERE raw_id = %s
            """,
            (raw_id,),
        )
