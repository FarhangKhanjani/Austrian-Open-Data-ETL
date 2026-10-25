"""Database helpers: connections and applying the versioned schema files in sql/schema/."""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg

logger = logging.getLogger("pipeline.db")

# src/pipeline/db.py -> repository root -> sql/schema
SCHEMA_DIR = Path(__file__).resolve().parents[2] / "sql" / "schema"


def connect(database_url: str) -> psycopg.Connection:
    """Open a connection with a bounded connect timeout."""
    return psycopg.connect(database_url, connect_timeout=10)


def schema_files(schema_dir: Path = SCHEMA_DIR) -> list[Path]:
    """Return the schema files in the order they must run (by numeric file prefix)."""
    files = sorted(schema_dir.glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"No .sql files found in {schema_dir}")
    return files


def apply_schema(conn: psycopg.Connection, schema_dir: Path = SCHEMA_DIR) -> list[str]:
    """Run every schema file in one transaction and return the applied file names.

    All files are written to be re-runnable (IF NOT EXISTS, ON CONFLICT), so this is
    safe to call on every deploy. One transaction means a broken file leaves the
    database exactly as it was.
    """
    applied: list[str] = []
    with conn.transaction():
        for path in schema_files(schema_dir):
            conn.execute(path.read_text(encoding="utf-8"))
            applied.append(path.name)
            logger.debug("applied schema file", extra={"ctx": {"file": path.name}})
    logger.info("schema applied", extra={"ctx": {"files": len(applied)}})
    return applied
