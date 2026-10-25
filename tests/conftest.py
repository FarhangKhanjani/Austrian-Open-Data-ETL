"""Shared fixtures. Integration tests use the Postgres from docker-compose.yml."""

from collections.abc import Iterator

import psycopg
import pytest

from pipeline import db
from pipeline.config import ConfigError, load_settings


@pytest.fixture
def pg_conn() -> Iterator[psycopg.Connection]:
    """Connection inside a transaction that is rolled back after the test.

    Postgres DDL is transactional, so even schema changes made by a test disappear.
    Skips (does not fail) when no database is reachable, so plain `pytest` works on a
    machine without Docker running.
    """
    try:
        settings = load_settings()
        conn = db.connect(settings.database_url)
    except (ConfigError, psycopg.OperationalError) as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")

    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()
