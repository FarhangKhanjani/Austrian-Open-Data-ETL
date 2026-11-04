"""Command line entry point: `python -m pipeline.run`.

Modes:
    daily     Default. Weather for the last 7 days (to pick up revisions), today's
              fuel price snapshot, and population if a new year is published.
    backfill  Weather for --start to --end, in monthly chunks. Fuel prices are skipped
              because E-Control has no history endpoint.

Exit codes: 0 success, 1 failed or partial (at least one source failed), 2 bad config.

Database setup flags (each runs alone and exits):
    --check-db  Verify that the database is reachable.
    --init-db   Create or update the warehouse schema from sql/schema/ (safe to re-run).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from pipeline import config, db, jobs, load
from pipeline.config import ConfigError, Settings, load_settings, mask_url
from pipeline.extract import HttpClient
from pipeline.load import SourceResult
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
    parser.add_argument(
        "--init-db", action="store_true", help="Apply the schema in sql/schema/, then exit."
    )
    args = parser.parse_args(argv)

    if args.check_db and args.init_db:
        parser.error("use --check-db or --init-db, not both")
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
    with db.connect(settings.database_url) as conn:
        version = conn.execute("SHOW server_version").fetchone()
    logger.info(
        "database reachable",
        extra={"ctx": {"url": mask_url(settings.database_url), "server_version": version[0]}},
    )


def overall_status(results: Sequence[SourceResult]) -> str:
    """'success' if nothing failed, 'failed' if everything failed, else 'partial'."""
    failed = [r for r in results if r.status == "failed"]
    if not failed:
        return "success"
    if len(failed) == len(results):
        return "failed"
    return "partial"


def run_pipeline(
    settings: Settings,
    args: argparse.Namespace,
    client: HttpClient | None = None,
    today: date | None = None,
) -> str:
    """Run all sources for the requested mode and return the overall run status.

    `client` and `today` are injectable so tests can run against fixtures on a fixed date.
    """
    today = today or datetime.now(config.LOCAL_TZ).date()
    if args.mode == "daily":
        start = today - timedelta(days=config.WEATHER_WINDOW_DAYS)
        end = today - timedelta(days=1)
    else:
        start, end = args.start, args.end
    client = client or HttpClient(timeout_seconds=settings.http_timeout_seconds)

    with db.connect(settings.database_url) as conn:
        # Re-runnable by design, so every run brings the schema up to date first.
        db.apply_schema(conn)
        run_id = load.start_run(
            conn, args.mode, start, end, settings.triggered_by, settings.git_sha
        )
        conn.commit()
        logger.info(
            "run started",
            extra={"ctx": {"run_id": run_id, "mode": args.mode, "start": start, "end": end}},
        )

        ctx = jobs.JobContext(conn=conn, client=client, run_id=run_id, today=today)
        results = [jobs.run_weather(ctx, start, end, check_freshness=args.mode == "daily")]
        if args.mode == "daily":
            results.append(jobs.run_fuel(ctx))
        else:
            results.append(
                SourceResult(
                    source="econtrol",
                    status="skipped",
                    error_message="backfill not possible: no history endpoint",
                )
            )
        results.append(jobs.run_population(ctx))

        for result in results:
            load.record_source_result(conn, run_id, result)
            logger.info(
                "source result",
                extra={
                    "ctx": {
                        "source": result.source,
                        "status": result.status,
                        "read": result.rows_read,
                        "loaded": result.rows_loaded,
                        "rejected": result.rows_rejected,
                        "error": result.error_message,
                    }
                },
            )
        status = overall_status(results)
        errors = "; ".join(
            f"{r.source}: {r.error_message}" for r in results if r.status == "failed"
        )
        load.finish_run(conn, run_id, status, errors[:1000] or None)
        conn.commit()

    logger.info("run finished", extra={"ctx": {"run_id": run_id, "status": status}})
    return status


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
        if args.init_db:
            with db.connect(settings.database_url) as conn:
                db.apply_schema(conn)
            return 0

        status = run_pipeline(settings, args)
        return 0 if status == "success" else 1
    except Exception:
        # Fail loudly: full traceback in the log and a non-zero exit code for CI.
        logger.exception("pipeline failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
