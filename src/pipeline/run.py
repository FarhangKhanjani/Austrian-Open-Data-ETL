"""Command line entry point: `python -m pipeline.run`.

Modes:
    daily     Default. Process the recent window (implemented in Milestone 3).
    backfill  Process an explicit date range given with --start and --end.

`--check-db` only verifies that the database is reachable, which is useful right
after `docker compose up -d` or when setting up the hosted database.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import date

import psycopg

from pipeline.config import ConfigError, Settings, load_settings, mask_url
from pipeline.logging_config import setup_logging

logger = logging.getLogger("pipeline.run")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse and validate command line arguments."""
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.run",
        description="Run the Austrian open data ETL pipeline.",
    )
    parser.add_argument("--mode", choices=("daily", "backfill"), default="daily")
    parser.add_argument("--start", type=date.fromisoformat, help="Backfill start date, YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, help="Backfill end date, YYYY-MM-DD")
    parser.add_argument(
        "--check-db", action="store_true", help="Only test the database connection, then exit."
    )
    args = parser.parse_args(argv)

    if args.mode == "backfill":
        if args.start is None or args.end is None:
            parser.error("--mode backfill needs both --start and --end")
        if args.start > args.end:
            parser.error("--start must be on or before --end")
    elif args.start is not None or args.end is not None:
        parser.error("--start and --end are only allowed with --mode backfill")

    return args


def check_database(settings: Settings) -> None:
    """Open a connection and log the server version. Raises on failure."""
    with psycopg.connect(settings.database_url, connect_timeout=10) as conn:
        version = conn.execute("SHOW server_version").fetchone()
    logger.info(
        "database reachable",
        extra={"ctx": {"url": mask_url(settings.database_url), "server_version": version[0]}},
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the pipeline and return a process exit code (0 on success)."""
    args = parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        # Logging is not configured yet, so report straight to stderr.
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    setup_logging(settings.log_level)
    logger.info("pipeline started", extra={"ctx": {"mode": args.mode}})

    try:
        if args.check_db:
            check_database(settings)
            return 0

        # Extract, validate, transform and load steps arrive in Milestone 3.
        logger.warning("no pipeline steps implemented yet")
        return 0
    except Exception:
        # Fail loudly: full traceback in the log and a non-zero exit code for CI.
        logger.exception("pipeline failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
