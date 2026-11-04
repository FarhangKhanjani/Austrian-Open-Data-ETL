"""HTTP client behaviour: retries, backoff, Retry-After, fail-fast, pacing. No real network."""

import hashlib
from datetime import date

import pytest
import requests
import responses

from pipeline import extract
from pipeline.extract import ExtractError, HttpClient

URL = "https://api.example.test/data"


class FakeTime:
    """Records requested sleeps and advances a fake clock instead of waiting."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        return self.now


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
def client(fake_time: FakeTime) -> HttpClient:
    return HttpClient(
        max_attempts=3,
        backoff_seconds=2.0,
        min_interval_seconds=0.0,
        sleep=fake_time.sleep,
        clock=fake_time.clock,
    )


@responses.activate
def test_success_returns_body_hash_and_metadata(client: HttpClient) -> None:
    responses.get(URL, body="a,b\n1,2\n", content_type="text/csv")

    fetched = client.get("geosphere", "klima-v2-1d", URL, {"start": "2026-10-01"})

    assert fetched.body == "a,b\n1,2\n"
    assert fetched.content_hash == hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    assert fetched.status_code == 200
    assert fetched.url == f"{URL}?start=2026-10-01"
    assert fetched.params == {"start": "2026-10-01"}
    assert fetched.fetched_at.tzinfo is not None


@responses.activate
def test_sends_identifying_user_agent(client: HttpClient) -> None:
    responses.get(URL, body="ok")
    client.get("econtrol", "fuel-by-region", URL)
    assert "austrian-open-data-etl" in responses.calls[0].request.headers["User-Agent"]


@responses.activate
def test_retries_server_errors_with_exponential_backoff(
    client: HttpClient, fake_time: FakeTime
) -> None:
    responses.get(URL, status=503)
    responses.get(URL, status=502)
    responses.get(URL, body="finally")

    assert client.get("geosphere", "klima-v2-1d", URL).body == "finally"
    assert len(responses.calls) == 3
    assert fake_time.sleeps == [2.0, 4.0]


@responses.activate
def test_respects_retry_after_header(client: HttpClient, fake_time: FakeTime) -> None:
    responses.get(URL, status=429, headers={"Retry-After": "7"})
    responses.get(URL, body="ok")

    client.get("geosphere", "klima-v2-1d", URL)

    assert fake_time.sleeps == [7.0]


@responses.activate
def test_client_errors_fail_fast_with_status_and_message(client: HttpClient) -> None:
    responses.get(URL, status=403, json={"detail": "station_ids '11035' not available"})

    with pytest.raises(ExtractError, match="HTTP 403.*11035") as exc_info:
        client.get("geosphere", "klima-v2-1d", URL)

    assert exc_info.value.status_code == 403
    assert len(responses.calls) == 1


@responses.activate
def test_gives_up_after_max_attempts_on_network_errors(client: HttpClient) -> None:
    responses.get(URL, body=requests.ConnectionError("connection reset"))

    with pytest.raises(ExtractError, match="giving up after 3 attempts.*connection reset"):
        client.get("econtrol", "fuel-by-region", URL)

    assert len(responses.calls) == 3


@responses.activate
def test_paces_requests_to_respect_rate_limits(fake_time: FakeTime) -> None:
    responses.get(URL, body="ok")
    client = HttpClient(min_interval_seconds=1.0, sleep=fake_time.sleep, clock=fake_time.clock)

    client.get("econtrol", "fuel-by-region", URL)
    fake_time.now += 0.25
    client.get("econtrol", "fuel-by-region", URL)

    assert fake_time.sleeps == [pytest.approx(0.75)]


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2026, 10, 5), date(2026, 10, 5), [(date(2026, 10, 5), date(2026, 10, 5))]),
        (
            date(2024, 1, 15),
            date(2024, 3, 10),
            [
                (date(2024, 1, 15), date(2024, 1, 31)),
                (date(2024, 2, 1), date(2024, 2, 29)),
                (date(2024, 3, 1), date(2024, 3, 10)),
            ],
        ),
        (
            date(2025, 12, 20),
            date(2026, 1, 5),
            [(date(2025, 12, 20), date(2025, 12, 31)), (date(2026, 1, 1), date(2026, 1, 5))],
        ),
    ],
)
def test_month_chunks(start: date, end: date, expected: list[tuple[date, date]]) -> None:
    assert list(extract.month_chunks(start, end)) == expected


@responses.activate
def test_geosphere_daily_request_parameters(client: HttpClient) -> None:
    responses.get(extract.config.GEOSPHERE_DAILY_URL, body="time,station\n")

    fetched = extract.fetch_geosphere_daily(
        client, [3202, 5925], date(2026, 10, 1), date(2026, 10, 7)
    )

    assert fetched.params["station_ids"] == "3202,5925"
    assert fetched.params["output_format"] == "csv"
    assert "rr_flag" in fetched.params["parameters"].split(",")
