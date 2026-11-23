"""Markdown run summary for the GitHub Actions job page.

GitHub sets GITHUB_STEP_SUMMARY to a file path; whatever is appended there is shown on
the run's summary page. That makes the outcome of a scheduled run visible at a glance
(rows loaded and rejected per source, failed data tests, data freshness) without
reading logs. Outside GitHub Actions the variable is unset and nothing is written.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import psycopg

from pipeline.load import SourceResult

STATUS_ICON = {"success": "✅", "partial": "⚠️", "failed": "❌", "skipped": "⏭️"}


def render(
    run_id: int,
    status: str,
    mode: str,
    window: tuple[date | None, date | None],
    results: Sequence[SourceResult],
    failed_tests: Sequence[str],
    freshness: Sequence[tuple[str, date | None, int | None]],
) -> str:
    """Build the summary text. Pure function, so it is easy to test."""
    start, end = window
    lines = [
        f"## {STATUS_ICON.get(status, '')} Pipeline run {run_id}: {status}",
        "",
        f"Mode `{mode}`, weather window {start} to {end}.",
        "",
        "| Source | Status | Read | Loaded | Rejected | Note |",
        "|---|---|---:|---:|---:|---|",
    ]
    for r in results:
        note = (r.error_message or "").replace("|", "/")
        lines.append(
            f"| {r.source} | {STATUS_ICON.get(r.status, '')} {r.status} | {r.rows_read} "
            f"| {r.rows_loaded} | {r.rows_rejected} | {note} |"
        )
    lines += ["", "**SQL data tests:** "]
    if failed_tests:
        lines[-1] += "❌ failed: " + ", ".join(f"`{name}`" for name in failed_tests)
    else:
        lines[-1] += "✅ all passed"
    lines += ["", "| Dataset | Latest data | Days behind |", "|---|---|---:|"]
    for dataset, latest, days_behind in freshness:
        lines.append(
            f"| {dataset} | {latest or 'none'} | {'' if days_behind is None else days_behind} |"
        )
    lines += ["", "Rejected rows are in `ops.quarantine` with their reasons.", ""]
    return "\n".join(lines)


def read_freshness(conn: psycopg.Connection) -> list[tuple[str, date | None, int | None]]:
    return conn.execute(
        "SELECT dataset, latest_data_date, days_behind FROM mart.data_freshness ORDER BY dataset"
    ).fetchall()


def write_github_summary(text: str) -> bool:
    """Append to the GitHub job summary if running in Actions. Returns True if written."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return False
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(text)
    return True
