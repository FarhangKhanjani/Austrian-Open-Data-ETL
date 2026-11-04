"""Data quality checks. Bad rows go to quarantine with a reason; nothing is dropped silently.

Two kinds of checks:

* Row checks, as pydantic models: types, required fields, value ranges, and rules
  across fields (min <= max). These ranges are deliberately stricter than the CHECK
  constraints in the database, which are only a last line of defence.
* Batch checks: duplicate keys inside one batch, freshness, completeness.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Record = dict[str, Any]


class _Row(BaseModel):
    # extra="forbid" catches a transform that emits a misspelled or unexpected field.
    model_config = ConfigDict(extra="forbid", frozen=True)


class WeatherStationRow(_Row):
    station_id: int = Field(gt=0)
    station_name: str = Field(min_length=1)
    bundesland_code: int = Field(ge=1, le=9)
    # Bounding box of Austria with a small margin.
    latitude: float = Field(ge=46.3, le=49.1)
    longitude: float = Field(ge=9.4, le=17.2)
    altitude_m: float | None = Field(default=None, ge=100, le=3800)
    valid_from: date | None = None
    valid_to: date | None = None
    is_active: bool


class WeatherDailyRow(_Row):
    station_id: int = Field(gt=0)
    obs_date: date
    # Austrian records are about -52 and +41 degrees C.
    temp_mean_c: float | None = Field(default=None, ge=-50, le=45)
    temp_max_c: float | None = Field(default=None, ge=-50, le=45)
    temp_min_c: float | None = Field(default=None, ge=-50, le=45)
    precip_mm: float | None = Field(default=None, ge=0, le=300)
    sunshine_h: float | None = Field(default=None, ge=0, le=24)
    min_quality_flag: int | None = None

    @model_validator(mode="after")
    def _min_not_above_max(self) -> WeatherDailyRow:
        if (
            self.temp_min_c is not None
            and self.temp_max_c is not None
            and self.temp_min_c > self.temp_max_c
        ):
            raise ValueError(
                f"temp_min_c ({self.temp_min_c}) is above temp_max_c ({self.temp_max_c})"
            )
        return self


class FuelPriceRow(_Row):
    snapshot_date: date
    bundesland_code: int = Field(ge=1, le=9)
    fuel_type: Literal["DIE", "SUP"]
    station_id: int = Field(gt=0)
    station_name: str = Field(min_length=1)
    postal_code: str | None = None
    city: str | None = None
    latitude: float | None = Field(default=None, ge=46.3, le=49.1)
    longitude: float | None = Field(default=None, ge=9.4, le=17.2)
    # Plausible retail range per litre; the database allows 0.5 to 5.0.
    price_eur: float = Field(ge=0.8, le=3.5)
    list_position: int = Field(ge=1)
    fetched_at: datetime


class PopulationRow(_Row):
    bundesland_code: int = Field(ge=1, le=9)
    reference_date: date
    # Smallest Bundesland (Burgenland) has about 300,000, the largest (Wien) about 2 million.
    population: int = Field(ge=100_000, le=5_000_000)


RowT = TypeVar("RowT", bound=_Row)


@dataclass(frozen=True)
class Rejected:
    """A record that failed validation, with every reason it failed."""

    record: Record
    reasons: list[str]


@dataclass
class ValidationResult(Generic[RowT]):
    valid: list[RowT] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)


def _describe(error: Any) -> str:
    """One pydantic error -> 'field: message (got value)'."""
    location = ".".join(str(part) for part in error["loc"]) or "row"
    message = error["msg"].removeprefix("Value error, ")
    if error["type"] == "value_error" or "input" not in error:
        return f"{location}: {message}"
    return f"{location}: {message} (got {error['input']!r})"


def validate_records(
    records: Iterable[Record], model: type[RowT], key_fields: Sequence[str]
) -> ValidationResult[RowT]:
    """Validate each record against `model` and reject duplicate keys within the batch.

    The first record with a given key wins; later ones are rejected with a reason,
    because two different values for one key means the source or the transform is
    wrong, and silently picking one would hide that.
    """
    result: ValidationResult[RowT] = ValidationResult()
    seen_keys: set[tuple[Any, ...]] = set()

    for record in records:
        try:
            row = model.model_validate(record)
        except ValidationError as exc:
            result.rejected.append(Rejected(record, [_describe(e) for e in exc.errors()]))
            continue

        key = tuple(getattr(row, name) for name in key_fields)
        if key in seen_keys:
            result.rejected.append(
                Rejected(record, [f"duplicate key {dict(zip(key_fields, key, strict=True))}"])
            )
            continue
        seen_keys.add(key)
        result.valid.append(row)

    return result


def reject_unknown_stations(
    result: ValidationResult[WeatherDailyRow], known_station_ids: set[int]
) -> None:
    """Move weather rows for stations missing from staging.weather_station to rejected.

    Without this, one unknown station would fail the whole batch on the foreign key.
    """
    kept: list[WeatherDailyRow] = []
    for row in result.valid:
        if row.station_id in known_station_ids:
            kept.append(row)
        else:
            result.rejected.append(
                Rejected(row.model_dump(), [f"station_id: unknown station {row.station_id}"])
            )
    result.valid = kept


def check_freshness(latest: date | None, today: date, max_lag_days: int) -> str | None:
    """Return a problem description if the newest date is missing or too old, else None."""
    if latest is None:
        return "no dated rows were loaded"
    if latest < today - timedelta(days=max_lag_days):
        return f"newest data is from {latest}, more than {max_lag_days} days before {today}"
    return None


def check_all_bundeslaender(codes: Iterable[int]) -> str | None:
    """Return a problem description unless all nine Bundesland codes are present."""
    missing = sorted(set(range(1, 10)) - set(codes))
    if missing:
        return f"missing Bundesland codes {missing}"
    return None
