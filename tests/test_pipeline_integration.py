"""End to end: the whole pipeline against Postgres, with every HTTP call mocked."""

import re
from datetime import date

import psycopg
import pytest
import responses
from conftest import fixture_text

from pipeline import config, run
from pipeline.config import Settings
from pipeline.extract import HttpClient

pytestmark = pytest.mark.integration

TODAY = date(2026, 10, 25)


def _mock_sources(econtrol_status: int = 200) -> None:
    responses.get(config.GEOSPHERE_METADATA_URL, body=fixture_text("geosphere_stations.json"))
    responses.get(config.GEOSPHERE_DAILY_URL, body=fixture_text("geosphere_daily.csv"))
    if econtrol_status == 200:
        responses.get(config.ECONTROL_REGION_URL, body=fixture_text("econtrol_region.json"))
    else:
        responses.get(config.ECONTROL_REGION_URL, status=econtrol_status)
    responses.get(
        re.compile(r"https://data\.statistik\.gv\.at/data/OGD_bevstandjbab2002_BevStand_2026\.csv"),
        body=fixture_text("population.csv"),
    )


def _run(database_url: str) -> str:
    settings = Settings(database_url=database_url, triggered_by="test")
    args = run.parse_args([])
    client = HttpClient(max_attempts=1, min_interval_seconds=0, sleep=lambda _s: None)
    return run.run_pipeline(settings, args, client=client, today=TODAY)


def _counts(database_url: str) -> dict[str, int]:
    tables = [
        "staging.weather_station",
        "staging.weather_daily",
        "staging.fuel_price",
        "staging.population",
    ]
    with psycopg.connect(database_url) as conn:
        return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}  # noqa: S608


@responses.activate
def test_daily_run_loads_valid_rows_and_quarantines_bad_ones(clean_database: str) -> None:
    _mock_sources()

    assert _run(clean_database) == "success"

    assert _counts(clean_database) == {
        "staging.weather_station": 2,  # Linz and Wien; the "Bayern" station is rejected
        "staging.weather_daily": 3,
        "staging.fuel_price": 18,  # 9 Bundesländer x 2 fuel types x 1 valid station
        "staging.population": 9,
    }
    with psycopg.connect(clean_database) as conn:
        quarantine = dict(
            conn.execute(
                "SELECT target_table, count(*) FROM ops.quarantine GROUP BY target_table"
            ).fetchall()
        )
        precip = conn.execute(
            """
            SELECT precip_mm FROM staging.weather_daily
            WHERE station_id = 3202 AND obs_date = '2026-10-24'
            """
        ).fetchone()[0]
        run_row = conn.execute(
            "SELECT status, rows_loaded, rows_rejected, triggered_by FROM ops.pipeline_run"
        ).fetchone()

    assert quarantine == {
        "staging.weather_station": 1,
        "staging.weather_daily": 4,  # min > max, duplicate, unparsable, unknown station
        "staging.fuel_price": 36,  # missing price and implausible price, 18 times
    }
    assert precip == 0  # stored as 0.0, not the source's -1 sentinel
    assert run_row == ("success", 2 + 3 + 18 + 9, 1 + 4 + 36, "test")


@responses.activate
def test_running_twice_creates_no_duplicates(clean_database: str) -> None:
    _mock_sources()
    _run(clean_database)
    after_first = _counts(clean_database)

    _run(clean_database)

    assert _counts(clean_database) == after_first
    with psycopg.connect(clean_database) as conn:
        statuses = conn.execute(
            """
            SELECT source, status FROM ops.pipeline_run_source
            WHERE run_id = (SELECT max(run_id) FROM ops.pipeline_run)
            ORDER BY source
            """
        ).fetchall()
        raw_count = conn.execute("SELECT count(*) FROM raw.api_response").fetchone()[0]

    # Population is not fetched again once the year is complete.
    assert statuses == [
        ("econtrol", "success"),
        ("geosphere", "success"),
        ("statistik_austria", "skipped"),
    ]
    # raw is append-only: run 1 stored 2 + 18 + 1 responses, run 2 another 2 + 18.
    assert raw_count == 41


@responses.activate
def test_failing_source_gives_partial_run_and_keeps_other_sources(clean_database: str) -> None:
    _mock_sources(econtrol_status=404)

    assert _run(clean_database) == "partial"

    counts = _counts(clean_database)
    assert counts["staging.weather_daily"] == 3
    assert counts["staging.fuel_price"] == 0
    with psycopg.connect(clean_database) as conn:
        status, error = conn.execute(
            "SELECT status, error_message FROM ops.pipeline_run"
        ).fetchone()
    assert status == "partial"
    assert "econtrol" in error and "HTTP 404" in error


@responses.activate
def test_stale_weather_fails_the_daily_run_loudly(clean_database: str) -> None:
    _mock_sources()
    settings = Settings(database_url=clean_database, triggered_by="test")
    client = HttpClient(max_attempts=1, min_interval_seconds=0, sleep=lambda _s: None)

    # Same fixture, but "today" is a week later: newest weather is 8 days old.
    status = run.run_pipeline(settings, run.parse_args([]), client=client, today=date(2026, 11, 1))

    assert status == "partial"
    with psycopg.connect(clean_database) as conn:
        error = conn.execute(
            "SELECT error_message FROM ops.pipeline_run_source WHERE source = 'geosphere'"
        ).fetchone()[0]
    assert "newest data is from 2026-10-24" in error
