"""Runtime configuration, read from environment variables.

Values come from the process environment. For local development a `.env` file is
loaded first (see `.env.example`). In GitHub Actions the same variables are set from
repository secrets, so the code path is identical everywhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

# Source endpoints and pipeline constants. These are facts about the sources, not
# per-environment settings, so they live in code rather than in environment variables.
LOCAL_TZ = ZoneInfo("Europe/Vienna")
USER_AGENT = (
    "austrian-open-data-etl/0.1 (+https://github.com/FarhangKhanjani/Austrian-Open-Data-ETL)"
)

GEOSPHERE_DAILY_URL = "https://dataset.api.hub.geosphere.at/v1/station/historical/klima-v2-1d"
GEOSPHERE_METADATA_URL = f"{GEOSPHERE_DAILY_URL}/metadata"
GEOSPHERE_PARAMETERS = ("tl_mittel", "tlmax", "tlmin", "rr", "so_h")
# The daily run re-fetches this many days so that GeoSphere's revisions are picked up.
WEATHER_WINDOW_DAYS = 7
# A daily run fails loudly if the newest weather date is older than this.
WEATHER_MAX_LAG_DAYS = 3

ECONTROL_REGION_URL = "https://api.e-control.at/sprit/1.0/search/gas-stations/by-region"
FUEL_TYPES = ("DIE", "SUP")

POPULATION_URL = "https://data.statistik.gv.at/data/OGD_bevstandjbab2002_BevStand_{year}.csv"


class ConfigError(ValueError):
    """Raised when a required setting is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """All settings the pipeline needs, validated once at startup."""

    database_url: str
    log_level: str = "INFO"
    http_timeout_seconds: float = 30.0
    triggered_by: str = "local"
    git_sha: str | None = None

    def __repr__(self) -> str:
        # Never let the password reach logs or tracebacks.
        return (
            f"Settings(database_url={mask_url(self.database_url)!r}, "
            f"log_level={self.log_level!r}, http_timeout_seconds={self.http_timeout_seconds}, "
            f"triggered_by={self.triggered_by!r}, git_sha={self.git_sha!r})"
        )


def mask_url(url: str) -> str:
    """Return the URL with its password replaced by `***`."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@", 1)
    return urlunsplit(parts._replace(netloc=netloc))


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build `Settings` from the environment.

    Args:
        env: Mapping to read from. Defaults to `os.environ` after loading `.env`.
            Tests pass a plain dict so they never depend on the developer's machine.

    Raises:
        ConfigError: If `DATABASE_URL` is missing or a value has the wrong format.
    """
    if env is None:
        load_dotenv()
        env = dict(os.environ)

    database_url = env.get("DATABASE_URL", "").strip()
    if not database_url:
        raise ConfigError("DATABASE_URL is not set. Copy .env.example to .env for local runs.")
    if urlsplit(database_url).scheme not in ("postgresql", "postgres"):
        raise ConfigError("DATABASE_URL must start with postgresql://")

    log_level = env.get("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in VALID_LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL must be one of {VALID_LOG_LEVELS}, got {log_level!r}")

    raw_timeout = env.get("HTTP_TIMEOUT_SECONDS", "30")
    try:
        http_timeout_seconds = float(raw_timeout)
    except ValueError as exc:
        raise ConfigError(f"HTTP_TIMEOUT_SECONDS must be a number, got {raw_timeout!r}") from exc
    if http_timeout_seconds <= 0:
        raise ConfigError("HTTP_TIMEOUT_SECONDS must be greater than 0")

    # Set automatically inside GitHub Actions; absent on a developer machine.
    triggered_by = env.get("GITHUB_EVENT_NAME", "local").strip() or "local"
    git_sha = env.get("GITHUB_SHA", "").strip() or None

    return Settings(
        database_url=database_url,
        log_level=log_level,
        http_timeout_seconds=http_timeout_seconds,
        triggered_by=triggered_by,
        git_sha=git_sha,
    )
