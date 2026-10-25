"""Tests for command line parsing and exit codes."""

import logging
from datetime import date

import pytest

from pipeline import config, run
from pipeline.logging_config import KeyValueFormatter


def test_default_mode_is_daily() -> None:
    args = run.parse_args([])
    assert args.mode == "daily"
    assert args.start is None


def test_backfill_parses_dates() -> None:
    args = run.parse_args(["--mode", "backfill", "--start", "2026-01-01", "--end", "2026-01-31"])
    assert (args.start, args.end) == (date(2026, 1, 1), date(2026, 1, 31))


@pytest.mark.parametrize(
    "argv",
    [
        ["--mode", "backfill"],
        ["--mode", "backfill", "--start", "2026-02-01", "--end", "2026-01-01"],
        ["--start", "2026-01-01"],
        ["--mode", "backfill", "--start", "01.01.2026", "--end", "2026-01-31"],
    ],
)
def test_invalid_arguments_exit_with_usage_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run.parse_args(argv)
    assert exc_info.value.code == 2


def test_missing_database_url_returns_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run, "load_settings", lambda: config.load_settings({}))
    assert run.main([]) == 2


def test_unexpected_error_returns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        run, "load_settings", lambda: config.load_settings({"DATABASE_URL": "postgresql://x@y/z"})
    )

    def boom(_settings: object) -> None:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(run, "check_database", boom)
    assert run.main(["--check-db"]) == 1


def test_formatter_appends_context() -> None:
    record = logging.LogRecord("pipeline.test", logging.INFO, __file__, 1, "loaded", None, None)
    record.ctx = {"rows": 42, "source": "geosphere"}
    line = KeyValueFormatter().format(record)
    assert line.endswith("pipeline.test | loaded rows=42 source=geosphere")
