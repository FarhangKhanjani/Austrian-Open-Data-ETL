"""Database helpers: connections, applying sql/schema/ and sql/views/, running sql/tests/."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import psycopg

logger = logging.getLogger("pipeline.db")

# src/pipeline/db.py -> repository root -> sql/
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
SCHEMA_DIR = SQL_DIR / "schema"
VIEWS_DIR = SQL_DIR / "views"
TESTS_DIR = SQL_DIR / "tests"


def connect(database_url: str) -> psycopg.Connection:
    """Open a connection with a bounded connect timeout."""
    return psycopg.connect(database_url, connect_timeout=10)


def sql_files(directory: Path) -> list[Path]:
    """Return the .sql files of a directory in run order (by file name)."""
    files = sorted(directory.glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"No .sql files found in {directory}")
    return files


def schema_files() -> list[Path]:
    """Tables first, then the views built on them."""
    return [*sql_files(SCHEMA_DIR), *sql_files(VIEWS_DIR)]


def apply_schema(conn: psycopg.Connection) -> list[str]:
    """Run every schema and view file in one transaction and return the applied names.

    All files are written to be re-runnable (IF NOT EXISTS, ON CONFLICT, DROP VIEW IF
    EXISTS), so this is safe to call on every run. One transaction means a broken file
    leaves the database exactly as it was.
    """
    applied: list[str] = []
    with conn.transaction():
        for path in schema_files():
            conn.execute(path.read_text(encoding="utf-8"))
            applied.append(f"{path.parent.name}/{path.name}")
            logger.debug("applied schema file", extra={"ctx": {"file": applied[-1]}})
    logger.info("schema applied", extra={"ctx": {"files": len(applied)}})
    return applied


@dataclass(frozen=True)
class SqlTestResult:
    """Outcome of one data test: it passes when its query returns no rows."""

    name: str
    failing_rows: int

    @property
    def passed(self) -> bool:
        return self.failing_rows == 0


def run_sql_tests(conn: psycopg.Connection, tests_dir: Path = TESTS_DIR) -> list[SqlTestResult]:
    """Run each query in sql/tests/. A query returns the rows that break its rule."""
    results = []
    for path in sql_files(tests_dir):
        rows = conn.execute(path.read_text(encoding="utf-8")).fetchall()
        result = SqlTestResult(name=path.stem, failing_rows=len(rows))
        results.append(result)
        if not result.passed:
            logger.error(
                "sql test failed",
                extra={"ctx": {"test": result.name, "failing_rows": len(rows), "first": rows[0]}},
            )
    conn.rollback()  # the tests only read; end the implicit transaction cleanly
    passed = sum(result.passed for result in results)
    logger.info("sql tests done", extra={"ctx": {"passed": passed, "total": len(results)}})
    return results
