"""Fetch raw payloads from GeoSphere, E-Control and Statistik Austria.

Extract does no parsing. It returns the response body exactly as received, plus the
metadata the raw layer needs (URL, status, fetch time, content hash). That keeps this
module small and makes every later step replayable from raw.api_response.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import requests

from pipeline import config

logger = logging.getLogger("pipeline.extract")

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 60.0


class ExtractError(RuntimeError):
    """A request failed for good: a non-retryable status, or retries were exhausted."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class FetchedResponse:
    """One HTTP response, ready to be stored in raw.api_response."""

    source: str
    endpoint: str
    url: str
    params: dict[str, str]
    status_code: int
    content_type: str | None
    body: str = field(repr=False)
    content_hash: str
    fetched_at: datetime


class HttpClient:
    """GET with timeouts, retries with exponential backoff, and polite pacing.

    Retries cover network errors and 429/5xx responses. A `Retry-After` header is
    respected (capped at 60 s). Other 4xx responses fail immediately, because retrying
    a bad request only wastes the source's capacity.

    `sleep` and `clock` are injectable so tests run instantly.
    """

    def __init__(
        self,
        timeout_seconds: float = 30.0,
        max_attempts: int = 4,
        backoff_seconds: float = 2.0,
        min_interval_seconds: float = 1.0,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.min_interval_seconds = min_interval_seconds
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = config.USER_AGENT
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None

    def get(
        self, source: str, endpoint: str, url: str, params: Mapping[str, str] | None = None
    ) -> FetchedResponse:
        """Fetch `url` and return the body with its metadata, or raise ExtractError."""
        params = dict(params or {})
        last_problem = "no attempt made"

        for attempt in range(1, self.max_attempts + 1):
            self._wait_for_rate_limit()
            wait_seconds = self.backoff_seconds * 2 ** (attempt - 1)
            try:
                response = self.session.get(url, params=params, timeout=self.timeout_seconds)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_problem = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 200:
                    return self._to_fetched(source, endpoint, params, response)
                if response.status_code not in RETRYABLE_STATUS:
                    raise ExtractError(
                        f"{source} {endpoint}: HTTP {response.status_code} from {response.url}: "
                        f"{response.text[:300]}",
                        status_code=response.status_code,
                    )
                last_problem = f"HTTP {response.status_code}"
                wait_seconds = _retry_after(response) or wait_seconds

            if attempt < self.max_attempts:
                logger.warning(
                    "request failed, retrying",
                    extra={
                        "ctx": {
                            "source": source,
                            "endpoint": endpoint,
                            "attempt": attempt,
                            "problem": last_problem,
                            "wait_s": round(wait_seconds, 1),
                        }
                    },
                )
                self._sleep(wait_seconds)

        raise ExtractError(
            f"{source} {endpoint}: giving up after {self.max_attempts} attempts ({last_problem})"
        )

    def _wait_for_rate_limit(self) -> None:
        now = self._clock()
        if self._last_request_at is not None:
            remaining = self.min_interval_seconds - (now - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._clock()

    @staticmethod
    def _to_fetched(
        source: str, endpoint: str, params: dict[str, str], response: requests.Response
    ) -> FetchedResponse:
        raw_bytes = response.content
        logger.info(
            "fetched",
            extra={"ctx": {"source": source, "endpoint": endpoint, "bytes": len(raw_bytes)}},
        )
        return FetchedResponse(
            source=source,
            endpoint=endpoint,
            url=response.url,
            params=params,
            status_code=response.status_code,
            content_type=response.headers.get("Content-Type"),
            body=raw_bytes.decode("utf-8"),
            content_hash=hashlib.sha256(raw_bytes).hexdigest(),
            fetched_at=datetime.now(UTC),
        )


def _retry_after(response: requests.Response) -> float | None:
    """Seconds from a numeric Retry-After header, capped; None if absent or unparsable."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return min(float(value), MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        return None


def month_chunks(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Split [start, end] into calendar-month pieces, so backfill requests stay small."""
    chunk_start = start
    while chunk_start <= end:
        next_month = (chunk_start.replace(day=1) + timedelta(days=32)).replace(day=1)
        chunk_end = min(next_month - timedelta(days=1), end)
        yield chunk_start, chunk_end
        chunk_start = chunk_end + timedelta(days=1)


def fetch_geosphere_stations(client: HttpClient) -> FetchedResponse:
    """Station metadata for the klima-v2-1d dataset (names, Bundesland, coordinates)."""
    return client.get("geosphere", "station-metadata", config.GEOSPHERE_METADATA_URL)


def fetch_geosphere_daily(
    client: HttpClient, station_ids: list[int], start: date, end: date
) -> FetchedResponse:
    """Daily values plus their quality flags for the given stations, as CSV."""
    flags = [f"{name}_flag" for name in config.GEOSPHERE_PARAMETERS]
    params = {
        "parameters": ",".join([*config.GEOSPHERE_PARAMETERS, *flags]),
        "station_ids": ",".join(str(station_id) for station_id in station_ids),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "output_format": "csv",
    }
    return client.get("geosphere", "klima-v2-1d", config.GEOSPHERE_DAILY_URL, params)


def fetch_econtrol_region(
    client: HttpClient, bundesland_code: int, fuel_type: str
) -> FetchedResponse:
    """Current cheapest stations for one Bundesland and fuel type."""
    params = {
        "code": str(bundesland_code),
        "type": "BL",
        "fuelType": fuel_type,
        "includeClosed": "false",
    }
    return client.get("econtrol", "fuel-by-region", config.ECONTROL_REGION_URL, params)


def fetch_population(client: HttpClient, year: int) -> FetchedResponse:
    """Statistik Austria population on 1 January of `year`, by municipality, sex and age."""
    url = config.POPULATION_URL.format(year=year)
    return client.get("statistik_austria", "population", url, {})
