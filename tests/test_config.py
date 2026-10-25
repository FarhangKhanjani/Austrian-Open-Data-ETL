"""Tests for settings loading and secret masking."""

import pytest

from pipeline.config import ConfigError, load_settings, mask_url

URL = "postgresql://etl:s3cret@localhost:5432/austria_etl"


def test_load_settings_defaults() -> None:
    settings = load_settings({"DATABASE_URL": URL})
    assert settings.database_url == URL
    assert settings.log_level == "INFO"
    assert settings.http_timeout_seconds == 30.0


def test_load_settings_normalises_log_level() -> None:
    settings = load_settings({"DATABASE_URL": URL, "LOG_LEVEL": " debug "})
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "DATABASE_URL is not set"),
        ({"DATABASE_URL": "mysql://x@y/z"}, "must start with postgresql://"),
        ({"DATABASE_URL": URL, "LOG_LEVEL": "LOUD"}, "LOG_LEVEL must be one of"),
        ({"DATABASE_URL": URL, "HTTP_TIMEOUT_SECONDS": "soon"}, "must be a number"),
        ({"DATABASE_URL": URL, "HTTP_TIMEOUT_SECONDS": "0"}, "greater than 0"),
    ],
)
def test_load_settings_rejects_bad_values(env: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_settings(env)


def test_mask_url_hides_password() -> None:
    assert mask_url(URL) == "postgresql://etl:***@localhost:5432/austria_etl"


def test_mask_url_without_password_is_unchanged() -> None:
    assert mask_url("postgresql://localhost/db") == "postgresql://localhost/db"


def test_settings_repr_never_contains_password() -> None:
    assert "s3cret" not in repr(load_settings({"DATABASE_URL": URL}))
