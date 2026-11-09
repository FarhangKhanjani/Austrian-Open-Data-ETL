"""Mart views on small hand-made data, so every expected number can be checked by hand."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from pipeline import db

pytestmark = pytest.mark.integration

UPPER_AUSTRIA, VIENNA = 4, 9
START = date(2026, 9, 1)
SPIKE_DAY = date(2026, 10, 10)
GAP_DAY = date(2026, 10, 5)


def _median_for(day: date) -> float:
    """Expected regional median: the two valley stations read base and base + 2."""
    if day == SPIKE_DAY:
        return 25.0
    base = 10.0 if (day - START).days % 2 == 0 else 12.0
    return base + 1.0


@pytest.fixture
def conn(clean_database: str) -> Iterator[psycopg.Connection]:
    with db.connect(clean_database) as connection:
        run_id = connection.execute(
            "INSERT INTO ops.pipeline_run (mode) VALUES ('daily') RETURNING run_id"
        ).fetchone()[0]
        raw_id = _insert_raw(connection, run_id)
        connection.execute(
            """
            INSERT INTO staging.weather_station
                (station_id, station_name, bundesland_code, latitude, longitude, altitude_m,
                 is_active)
            VALUES (1, 'Valley A', 4, 48.3, 14.3, 300, true),
                   (2, 'Valley B', 4, 48.2, 14.1, 400, true),
                   (3, 'Mountain', 4, 47.6, 13.6, 2500, true),
                   (4, 'Wien', 9, 48.2, 16.4, 200, true)
            """
        )
        rows = []
        day = START
        while day <= SPIKE_DAY:
            if day != GAP_DAY:
                low = _median_for(day) - 1.0
                rows += [(1, day, low, 0.0), (2, day, low + 2.0, 0.0), (3, day, -10.0, 0.0)]
            day += timedelta(days=1)
        # Vienna: the first five days of October in 2025 and 2026, for year-over-year.
        for year, temp, precip in ((2025, 8.0, 2.0), (2026, 11.0, 3.0)):
            for d in range(1, 6):
                rows.append((4, date(year, 10, d), temp, precip))
        with connection.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO staging.weather_daily
                    (station_id, obs_date, temp_mean_c, precip_mm, sunshine_h, raw_id)
                VALUES (%s, %s, %s, %s, 5.0, %s)
                """,
                [(*row, raw_id) for row in rows],
            )
        _insert_fuel(connection, raw_id)
        connection.commit()
        yield connection


def _insert_raw(conn: psycopg.Connection, run_id: int) -> int:
    return conn.execute(
        """
        INSERT INTO raw.api_response
            (run_id, source, endpoint, source_url, http_status, body, content_hash, fetched_at)
        VALUES (%s, 'econtrol', 'test', 'https://example.test', 200, '[]', %s, now())
        RETURNING raw_id
        """,
        (run_id, "0" * 64),
    ).fetchone()[0]


def _insert_fuel(conn: psycopg.Connection, raw_id: int) -> None:
    prices = [
        # (day, Bundesland, station, price)
        (date(2026, 10, 8), UPPER_AUSTRIA, 10, "1.600"),
        (date(2026, 10, 8), UPPER_AUSTRIA, 11, "1.640"),
        (date(2026, 10, 8), VIENNA, 20, "1.700"),
        (date(2026, 10, 9), UPPER_AUSTRIA, 10, "1.650"),
        (date(2026, 10, 9), VIENNA, 20, "1.620"),
        (date(2026, 10, 11), UPPER_AUSTRIA, 10, "1.610"),  # a day with no weather yet
    ]
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO staging.fuel_price
                (snapshot_date, bundesland_code, fuel_type, station_id, station_name,
                 price_eur, list_position, fetched_at, raw_id)
            VALUES (%s, %s, 'DIE', %s, 'Station', %s, 1, %s, %s)
            """,
            [
                (d, bl, st, p, datetime(2026, 10, 11, 11, tzinfo=UTC), raw_id)
                for d, bl, st, p in prices
            ],
        )


def _weather(conn: psycopg.Connection, bundesland: int, day: date) -> dict:
    cur = conn.execute(
        "SELECT * FROM mart.weather_daily_bundesland WHERE bundesland_code = %s AND obs_date = %s",
        (bundesland, day),
    )
    names = [column.name for column in cur.description]
    return dict(zip(names, cur.fetchone(), strict=True))


def test_regional_median_ignores_mountain_stations(conn: psycopg.Connection) -> None:
    row = _weather(conn, UPPER_AUSTRIA, date(2026, 10, 1))
    assert row["temp_mean_c"] == Decimal(str(_median_for(date(2026, 10, 1))))
    assert row["stations_reporting"] == 2


def test_rolling_7_day_average_uses_calendar_days_across_a_gap(conn: psycopg.Connection) -> None:
    day = date(2026, 10, 7)
    window = [day - timedelta(days=n) for n in range(7)]
    expected = sum(_median_for(d) for d in window if d != GAP_DAY) / 6  # 6 days, one missing

    row = _weather(conn, UPPER_AUSTRIA, day)

    assert row["temp_mean_7d_avg"] == Decimal(str(round(expected, 1)))


def test_day_over_day_change_is_null_after_a_missing_day(conn: psycopg.Connection) -> None:
    assert _weather(conn, UPPER_AUSTRIA, date(2026, 10, 6))["temp_change_vs_prev_day"] is None

    change = _weather(conn, UPPER_AUSTRIA, date(2026, 10, 7))["temp_change_vs_prev_day"]
    assert change == Decimal(str(_median_for(date(2026, 10, 7)) - _median_for(date(2026, 10, 6))))


def test_spike_is_flagged_as_anomaly_and_normal_days_are_not(conn: psycopg.Connection) -> None:
    spike = _weather(conn, UPPER_AUSTRIA, SPIKE_DAY)
    normal = _weather(conn, UPPER_AUSTRIA, date(2026, 10, 9))

    assert spike["is_temp_anomaly"] is True
    assert spike["temp_z_score"] > 2
    assert normal["is_temp_anomaly"] is False


def test_monthly_compares_with_same_month_previous_year(conn: psycopg.Connection) -> None:
    row = conn.execute(
        """
        SELECT temp_diff_vs_prev_year_c, precip_pct_change_vs_prev_year, is_complete_month
        FROM mart.weather_monthly_bundesland
        WHERE bundesland_code = 9 AND month_start = '2026-10-01'
        """
    ).fetchone()
    assert row == (Decimal("3.0"), Decimal("50.0"), False)


def test_fuel_change_rank_and_premium(conn: psycopg.Connection) -> None:
    rows = conn.execute(
        """
        SELECT bundesland_code, cheapest_price_eur, change_vs_prev_day_eur,
               pct_change_vs_prev_day, price_rank, premium_vs_cheapest_region_eur,
               stations_listed
        FROM mart.fuel_price_daily
        WHERE snapshot_date = '2026-10-09'
        ORDER BY price_rank
        """
    ).fetchall()
    assert rows == [
        (VIENNA, Decimal("1.620"), Decimal("-0.080"), Decimal("-4.71"), 1, Decimal("0.000"), 1),
        (
            UPPER_AUSTRIA,
            Decimal("1.650"),
            Decimal("0.050"),
            Decimal("3.13"),
            2,
            Decimal("0.030"),
            1,
        ),
    ]


def test_fuel_change_is_null_when_previous_snapshot_is_not_yesterday(
    conn: psycopg.Connection,
) -> None:
    change = conn.execute(
        """
        SELECT change_vs_prev_day_eur FROM mart.fuel_price_daily
        WHERE snapshot_date = '2026-10-11' AND bundesland_code = 4
        """
    ).fetchone()[0]
    assert change is None


def test_snapshot_keeps_days_that_only_one_source_has(conn: psycopg.Connection) -> None:
    rows = {
        day: (has_weather, has_fuel)
        for day, has_weather, has_fuel in conn.execute(
            """
            SELECT date, has_weather, has_fuel FROM mart.regional_daily_snapshot
            WHERE bundesland_code = 4 AND date IN ('2026-10-08', '2026-10-10', '2026-10-11')
            """
        )
    }
    assert rows == {
        date(2026, 10, 8): (True, True),
        date(2026, 10, 10): (True, False),
        date(2026, 10, 11): (False, True),
    }


def test_data_freshness_has_one_row_per_source(conn: psycopg.Connection) -> None:
    rows = conn.execute(
        "SELECT source, latest_data_date FROM mart.data_freshness ORDER BY source"
    ).fetchall()
    assert rows == [
        ("econtrol", date(2026, 10, 11)),
        ("geosphere", SPIKE_DAY),
        ("statistik_austria", None),
    ]


def test_sql_data_tests_pass_on_clean_data(conn: psycopg.Connection) -> None:
    failures = [r for r in db.run_sql_tests(conn) if not r.passed]
    assert failures == []


def test_sql_data_tests_catch_planted_problems(conn: psycopg.Connection) -> None:
    run_id = conn.execute("SELECT max(run_id) FROM ops.pipeline_run").fetchone()[0]
    other_raw_id = _insert_raw(conn, run_id)
    # A second response in an existing snapshot, and a weather date in the future.
    conn.execute(
        """
        INSERT INTO staging.fuel_price
            (snapshot_date, bundesland_code, fuel_type, station_id, station_name,
             price_eur, list_position, fetched_at, raw_id)
        VALUES ('2026-10-08', 4, 'DIE', 99, 'Leftover', 1.7, 9, now(), %s)
        """,
        (other_raw_id,),
    )
    conn.execute(
        """
        INSERT INTO staging.weather_daily (station_id, obs_date, temp_mean_c, raw_id)
        VALUES (1, current_date + 30, 5.0, %s)
        """,
        (other_raw_id,),
    )

    failed = {r.name for r in db.run_sql_tests(conn) if not r.passed}

    assert failed == {"staging_fuel_snapshot_is_one_fetch", "staging_weather_has_no_future_dates"}
