"""Shared fixtures.

Database tests run against a separate database (`<your db>_test`) on the Postgres from
docker-compose.yml, so they can never overwrite development data. It is created on
first use. Set TEST_DATABASE_URL to point somewhere else.

Locally, database tests are skipped when Postgres is not running. In CI (where GitHub
sets CI=true) they fail instead, so a green build always means they really ran.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from pipeline import db
from pipeline.config import ConfigError, load_settings

FIXTURES = Path(__file__).parent / "fixtures"

DATA_TABLES = (
    "ops.quarantine",
    "ops.pipeline_run_source",
    "staging.weather_daily",
    "staging.weather_station",
    "staging.fuel_price",
    "staging.population",
    "raw.api_response",
    "ops.pipeline_run",
)


def _unavailable(reason: str) -> None:
    """Skip locally, fail in CI."""
    if os.environ.get("CI") == "true":
        pytest.fail(f"database required in CI: {reason}")
    pytest.skip(reason)


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """URL of the test database, created if needed."""
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit

    try:
        base_url = load_settings().database_url
    except ConfigError as exc:
        _unavailable(f"PostgreSQL not configured: {exc}")

    parts = urlsplit(base_url)
    test_db = parts.path.lstrip("/") + "_test"
    try:
        with psycopg.connect(base_url, connect_timeout=5, autocommit=True) as admin:
            exists = admin.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (test_db,)
            ).fetchone()
            if not exists:
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(test_db)))
    except psycopg.OperationalError as exc:
        _unavailable(f"PostgreSQL not available: {exc}")

    return urlunsplit(parts._replace(path=f"/{test_db}"))


@pytest.fixture
def clean_database(test_database_url: str) -> str:
    """Test database with the current schema and empty data tables (for committed writes)."""
    with db.connect(test_database_url) as conn:
        db.apply_schema(conn)
        conn.execute(f"TRUNCATE {', '.join(DATA_TABLES)} RESTART IDENTITY CASCADE")
        conn.commit()
    return test_database_url


@pytest.fixture
def pg_conn(clean_database: str) -> Iterator[psycopg.Connection]:
    """Connection to an empty test database, inside a transaction rolled back afterwards.

    Postgres DDL is transactional, so even schema changes made by a test disappear.
    """
    conn = db.connect(clean_database)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()
