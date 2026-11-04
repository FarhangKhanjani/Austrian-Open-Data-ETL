"""Parse raw response bodies into plain records for validation.

These functions are pure: text in, list of dicts out, no network and no database.
They only reshape and translate source conventions (for example GeoSphere's `-1`
meaning "no precipitation"). Judging whether a value is plausible is validate.py's job,
so a value that cannot be parsed is passed through unchanged and rejected there with
a readable reason, instead of crashing the run here.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from datetime import date, datetime
from typing import Any

from pipeline import config

Record = dict[str, Any]

# Mirrors staging.bundesland (sql/schema/040_reference_data.sql). GeoSphere names the
# Bundesland in German; E-Control and Statistik Austria use the numeric codes directly.
BUNDESLAND_CODES = {
    "Burgenland": 1,
    "Kärnten": 2,
    "Niederösterreich": 3,
    "Oberösterreich": 4,
    "Salzburg": 5,
    "Steiermark": 6,
    "Tirol": 7,
    "Vorarlberg": 8,
    "Wien": 9,
}

# GeoSphere column -> staging.weather_daily column
WEATHER_COLUMNS = {
    "tl_mittel": "temp_mean_c",
    "tlmax": "temp_max_c",
    "tlmin": "temp_min_c",
    "rr": "precip_mm",
    "so_h": "sunshine_h",
}

GEOSPHERE_NO_PRECIPITATION = -1.0


def _number(value: str | None) -> float | str | None:
    """Empty -> None, numeric text -> float, anything else is returned unchanged."""
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return value


def _date_part(value: str) -> str:
    """'2026-10-24T00:00+00:00' -> '2026-10-24' (validation turns it into a date)."""
    return value[:10]


def parse_geosphere_stations(body: str) -> list[Record]:
    """Station metadata JSON -> one record per station."""
    payload = json.loads(body)
    return [
        {
            "station_id": station.get("id"),
            "station_name": station.get("name"),
            "bundesland_code": BUNDESLAND_CODES.get(station.get("state")),
            "latitude": station.get("lat"),
            "longitude": station.get("lon"),
            "altitude_m": station.get("altitude"),
            "valid_from": (station.get("valid_from") or "")[:10] or None,
            "valid_to": (station.get("valid_to") or "")[:10] or None,
            "is_active": station.get("is_active"),
        }
        for station in payload["stations"]
    ]


def parse_geosphere_daily(body: str) -> tuple[list[Record], int]:
    """Daily CSV -> (records, number of rows skipped because every value was empty).

    GeoSphere returns a row for every station and day in the request, including days
    that have no data yet (today) and stations that do not measure anything. Those rows
    carry no information, so they are dropped and counted, not quarantined.
    """
    records: list[Record] = []
    skipped_empty = 0

    for row in csv.DictReader(io.StringIO(body)):
        values = {target: _number(row.get(source)) for source, target in WEATHER_COLUMNS.items()}
        if all(value is None for value in values.values()):
            skipped_empty += 1
            continue

        if values["precip_mm"] == GEOSPHERE_NO_PRECIPITATION:
            values["precip_mm"] = 0.0

        flags = [_number(row.get(f"{source}_flag")) for source in WEATHER_COLUMNS]
        numeric_flags = [flag for flag in flags if isinstance(flag, float)]

        records.append(
            {
                "station_id": _number(row.get("station")),
                "obs_date": _date_part(row.get("time", "")),
                **values,
                "min_quality_flag": int(min(numeric_flags)) if numeric_flags else None,
            }
        )

    return records, skipped_empty


def parse_econtrol_region(
    body: str, bundesland_code: int, fuel_type: str, fetched_at: datetime
) -> list[Record]:
    """E-Control station list -> one record per station with a price for `fuel_type`.

    The snapshot date is the fetch date in Vienna time, so a run shortly after midnight
    UTC still files the prices under the Austrian calendar day.
    """
    snapshot_date = fetched_at.astimezone(config.LOCAL_TZ).date()
    records: list[Record] = []

    for position, station in enumerate(json.loads(body), start=1):
        location = station.get("location") or {}
        price = next(
            (
                entry.get("amount")
                for entry in station.get("prices") or []
                if entry.get("fuelType") == fuel_type
            ),
            None,
        )
        records.append(
            {
                "snapshot_date": snapshot_date,
                "bundesland_code": bundesland_code,
                "fuel_type": fuel_type,
                "station_id": station.get("id"),
                "station_name": station.get("name"),
                "postal_code": location.get("postalCode"),
                "city": location.get("city"),
                "latitude": location.get("latitude"),
                "longitude": location.get("longitude"),
                "price_eur": price,
                "list_position": station.get("position") or position,
                "fetched_at": fetched_at,
            }
        )

    return records


def parse_population(body: str) -> list[Record]:
    """Statistik Austria population CSV -> one record per Bundesland and year.

    The file has one row per municipality, sex and age (about 400,000 rows). The first
    digit of the municipality code is the Bundesland code, so summing by it gives the
    population per Bundesland.
    """
    totals: Counter[tuple[int, int]] = Counter()
    reader = csv.DictReader(io.StringIO(body.lstrip("﻿")), delimiter=";")

    for row in reader:
        year = int(row["C-A10-0"].removeprefix("A10-"))
        bundesland_code = int(row["C-GRGEMAKT-0"].removeprefix("GRGEMAKT-")[0])
        totals[(bundesland_code, year)] += int(row["F-ISIS-1"])

    return [
        {
            "bundesland_code": bundesland_code,
            "reference_date": date(year, 1, 1),
            "population": population,
        }
        for (bundesland_code, year), population in sorted(totals.items())
    ]
