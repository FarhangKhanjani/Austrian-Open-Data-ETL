"""Static checks on sql/ that need no database."""

import re

import pytest

from pipeline.db import SCHEMA_DIR, TESTS_DIR, VIEWS_DIR, schema_files, sql_files


@pytest.mark.parametrize("directory", [SCHEMA_DIR, VIEWS_DIR, TESTS_DIR])
def test_sql_files_are_named_for_ordering(directory) -> None:
    names = [path.name for path in sql_files(directory)]
    pattern = r"^[a-z_]+\.sql$" if directory == TESTS_DIR else r"^\d{3}_[a-z_]+\.sql$"
    assert all(re.match(pattern, name) for name in names), names


def test_tables_are_applied_before_views() -> None:
    dirs = [path.parent for path in schema_files()]
    assert dirs == sorted(dirs, key=lambda d: d != SCHEMA_DIR)


def test_every_table_statement_is_rerunnable() -> None:
    """Schema files run on every deploy, so a bare CREATE would fail the second time."""
    offenders = []
    for path in sql_files(SCHEMA_DIR):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            statement = line.strip().upper()
            if statement.startswith("CREATE ") and "IF NOT EXISTS" not in statement:
                offenders.append(f"{path.name}:{line_no}: {line.strip()}")
    assert offenders == []


def test_every_view_is_dropped_before_it_is_created() -> None:
    for path in sql_files(VIEWS_DIR):
        text = path.read_text(encoding="utf-8")
        created = re.findall(r"^CREATE VIEW (\S+) AS", text, flags=re.MULTILINE)
        dropped = re.findall(r"^DROP VIEW IF EXISTS (\S+) CASCADE;", text, flags=re.MULTILINE)
        assert created, f"{path.name} creates no view"
        assert created == dropped, path.name
        assert all(name.startswith("mart.") for name in created), path.name
