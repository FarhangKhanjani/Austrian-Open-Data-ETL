"""GitHub job summary: content and when it is written."""

from datetime import date
from pathlib import Path

import pytest

from pipeline import summary
from pipeline.load import SourceResult

RESULTS = [
    SourceResult("geosphere", "success", 3814, 3800, 14),
    SourceResult("econtrol", "failed", 0, 0, 0, "Bundesland 4 DIE: HTTP 503 | retried"),
    SourceResult("statistik_austria", "skipped", error_message="population already loaded"),
]
FRESHNESS = [("Fuel prices", date(2026, 11, 22), 1), ("Population", None, None)]


def _render(failed_tests: list[str]) -> str:
    return summary.render(
        42,
        "partial",
        "daily",
        (date(2026, 11, 15), date(2026, 11, 22)),
        RESULTS,
        failed_tests,
        FRESHNESS,
    )


def test_summary_shows_status_counts_and_freshness() -> None:
    text = _render([])
    assert text.startswith("## ⚠️ Pipeline run 42: partial")
    assert "| geosphere | ✅ success | 3814 | 3800 | 14 |  |" in text
    assert "✅ all passed" in text
    assert "| Fuel prices | 2026-11-22 | 1 |" in text
    assert "| Population | none |  |" in text


def test_pipe_characters_in_errors_do_not_break_the_table() -> None:
    assert "HTTP 503 / retried" in _render([])


def test_failed_sql_tests_are_listed() -> None:
    text = _render(["mart_snapshot_key_is_unique"])
    assert "❌ failed: `mart_snapshot_key_is_unique`" in text


def test_summary_written_only_inside_github_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert summary.write_github_summary("x") is False

    target = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
    assert summary.write_github_summary("first\n") is True
    assert summary.write_github_summary("second\n") is True
    assert target.read_text(encoding="utf-8") == "first\nsecond\n"
