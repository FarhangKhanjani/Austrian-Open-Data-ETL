"""Row and batch validation: what gets rejected, and that the reasons are readable."""

from datetime import UTC, date, datetime

import pytest
from conftest import fixture_text

from pipeline import transform, validate
from pipeline.validate import WeatherDailyRow


def _weather_result() -> validate.ValidationResult[WeatherDailyRow]:
    records, _ = transform.parse_geosphere_daily(fixture_text("geosphere_daily.csv"))
    return validate.validate_records(records, WeatherDailyRow, ["station_id", "obs_date"])


def _reasons(result: validate.ValidationResult) -> list[str]:
    return [reason for rejected in result.rejected for reason in rejected.reasons]


def test_weather_fixture_splits_into_valid_and_rejected() -> None:
    result = _weather_result()
    assert {(r.station_id, r.obs_date.isoformat()) for r in result.valid} == {
        (3202, "2026-10-23"),
        (3202, "2026-10-24"),
        (5925, "2026-10-23"),
        (9999, "2026-10-24"),
    }
    assert len(result.rejected) == 3


def test_min_above_max_is_rejected_with_values_in_reason() -> None:
    assert "row: temp_min_c (14.8) is above temp_max_c (7.0)" in _reasons(_weather_result())


def test_all_problems_of_a_row_are_reported_together() -> None:
    bad_row = next(r for r in _weather_result().rejected if r.record["temp_mean_c"] == "x")
    assert len(bad_row.reasons) == 2
    assert bad_row.reasons[0].startswith("temp_mean_c: Input should be a valid number")
    assert bad_row.reasons[1] == "sunshine_h: Input should be less than or equal to 24 (got 30.0)"


def test_duplicate_key_in_batch_is_rejected_and_first_row_wins() -> None:
    result = _weather_result()
    linz_23 = next(r for r in result.valid if r.station_id == 3202 and r.obs_date.day == 23)
    assert linz_23.temp_mean_c == 7.7
    assert any(reason.startswith("duplicate key") for reason in _reasons(result))


def test_unknown_station_moves_to_rejected() -> None:
    result = _weather_result()
    validate.reject_unknown_stations(result, known_station_ids={3202, 5925})

    assert all(row.station_id != 9999 for row in result.valid)
    assert "station_id: unknown station 9999" in _reasons(result)


def test_unexpected_field_is_rejected() -> None:
    record = {"station_id": 1, "obs_date": "2026-10-24", "temperature": 5.0}
    result = validate.validate_records([record], WeatherDailyRow, ["station_id", "obs_date"])
    assert "temperature: Extra inputs are not permitted (got 5.0)" in _reasons(result)


def test_station_outside_austria_or_unknown_state_is_rejected() -> None:
    records = transform.parse_geosphere_stations(fixture_text("geosphere_stations.json"))
    result = validate.validate_records(records, validate.WeatherStationRow, ["station_id"])
    rejected_ids = {r.record["station_id"] for r in result.rejected}
    assert rejected_ids == {7777}


def test_fuel_price_rules() -> None:
    records = transform.parse_econtrol_region(
        fixture_text("econtrol_region.json"), 4, "DIE", datetime(2026, 10, 25, 11, tzinfo=UTC)
    )
    result = validate.validate_records(records, validate.FuelPriceRow, ["station_id"])

    assert [row.station_id for row in result.valid] == [33597]
    assert _reasons(result) == [
        "price_eur: Input should be a valid number (got None)",
        "price_eur: Input should be less than or equal to 3.5 (got 16.49)",
    ]


@pytest.mark.parametrize(
    ("latest", "expected_problem"),
    [
        (date(2026, 10, 24), None),
        (date(2026, 10, 22), None),
        (date(2026, 10, 21), "newest data is from 2026-10-21, more than 3 days before 2026-10-25"),
        (None, "no dated rows were loaded"),
    ],
)
def test_freshness(latest: date | None, expected_problem: str | None) -> None:
    assert validate.check_freshness(latest, date(2026, 10, 25), 3) == expected_problem


def test_all_bundeslaender_required() -> None:
    assert validate.check_all_bundeslaender(range(1, 10)) is None
    assert validate.check_all_bundeslaender([1, 2, 3, 4, 5, 6, 7, 9]) == (
        "missing Bundesland codes [8]"
    )
