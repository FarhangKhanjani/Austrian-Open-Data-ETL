"""Static checks on sql/schema/ that need no database."""

import re

from pipeline.db import SCHEMA_DIR, schema_files


def test_schema_files_run_in_numeric_order() -> None:
    names = [path.name for path in schema_files()]
    assert names == sorted(names)
    assert all(re.match(r"^\d{3}_[a-z_]+\.sql$", name) for name in names)


def test_every_create_statement_is_rerunnable() -> None:
    """--init-db runs on every deploy, so a bare CREATE would fail the second time."""
    offenders = []
    for path in SCHEMA_DIR.glob("*.sql"):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            statement = line.strip().upper()
            if statement.startswith("CREATE ") and "IF NOT EXISTS" not in statement:
                offenders.append(f"{path.name}:{line_no}: {line.strip()}")
    assert offenders == []
