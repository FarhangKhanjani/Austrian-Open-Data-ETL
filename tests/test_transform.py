"""Parsing raw payloads into records, using small copies of real responses."""

from datetime import UTC, date, datetime

from conftest import fixture_text

from pipeline import transform


def test_stations_map_state_name_to_bundesland_code() -> None:
    records = transform.parse_geosphere_stations(fixture_text("geosphere_stations.json"))
    by_id = {r["station_id"]: r for r in records}

    assert by_id[3202]["bundesland_code"] == 4
    assert by_id[5925]["bundesland_code"] == 9
    assert by_id[3202]["valid_from"] == "1991-01-01"
    # An unknown state is passed through as None; validation rejects it with a reason.
    assert by_id[7777]["bundesland_code"] is None


def test_weather_converts_source_conventions() -> None:
    records, skipped = transform.parse_geosphere_daily(fixture_text("geosphere_daily.csv"))
    linz_24 = next(r for r in records if r["station_id"] == 3202 and r["obs_date"] == "2026-10-24")

    assert linz_24["precip_mm"] == 0.0  # GeoSphere -1 means "no precipitation"
    assert linz_24["min_quality_flag"] == 10  # lowest flag among the row's parameters
    assert linz_24["temp_mean_c"] == 8.7


def test_weather_drops_rows_without_any_values() -> None:
    records, skipped = transform.parse_geosphere_daily(fixture_text("geosphere_daily.csv"))

    assert skipped == 1  # today's row, not published yet
    assert all(r["obs_date"] != "2026-10-25" for r in records)
    assert len(records) == 7


def test_weather_keeps_unparsable_values_for_validation() -> None:
    records, _ = transform.parse_geosphere_daily(fixture_text("geosphere_daily.csv"))
    assert any(r["temp_mean_c"] == "x" for r in records)


def test_weather_missing_precipitation_stays_null() -> None:
    records, _ = transform.parse_geosphere_daily(fixture_text("geosphere_daily.csv"))
    wien_23 = next(r for r in records if r["station_id"] == 5925 and r["obs_date"] == "2026-10-23")
    assert wien_23["precip_mm"] is None
    assert wien_23["min_quality_flag"] == 0


def test_fuel_snapshot_date_uses_vienna_time() -> None:
    # 23:30 UTC on 24 October is already 25 October in Vienna (UTC+1 in winter).
    fetched_at = datetime(2026, 10, 25, 23, 30, tzinfo=UTC)

    records = transform.parse_econtrol_region(
        fixture_text("econtrol_region.json"), 4, "DIE", fetched_at
    )

    assert {r["snapshot_date"] for r in records} == {date(2026, 10, 26)}
    assert [r["price_eur"] for r in records] == [1.644, None, 16.49]
    assert records[0]["postal_code"] == "4652"
    assert [r["list_position"] for r in records] == [1, 2, 3]


def test_fuel_picks_price_for_requested_fuel_type() -> None:
    fetched_at = datetime(2026, 10, 25, 11, 30, tzinfo=UTC)
    records = transform.parse_econtrol_region(
        fixture_text("econtrol_region.json"), 4, "SUP", fetched_at
    )
    assert records[0]["price_eur"] == 1.689


def test_population_sums_municipalities_per_bundesland() -> None:
    records = transform.parse_population(fixture_text("population.csv"))

    assert len(records) == 9
    assert records[0] == {
        "bundesland_code": 1,
        "reference_date": date(2026, 1, 1),
        "population": 150_001,
    }
    assert records[-1]["population"] == 150_009
