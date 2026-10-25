"""Logging setup shared by every pipeline module.

One line per event, UTC timestamps, and `key=value` context so logs are readable in a
terminal and in the GitHub Actions log viewer, and still easy to grep.
"""

from __future__ import annotations

import logging
import sys
import time


class KeyValueFormatter(logging.Formatter):
    """Format records as `<utc time> <level> <logger> | <message> key=value ...`.

    Extra context is passed with `logger.info("msg", extra={"ctx": {...}})`.
    """

    converter = time.gmtime

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        ctx = getattr(record, "ctx", None)
        if ctx:
            line += " " + " ".join(f"{key}={value}" for key, value in ctx.items())
        return line


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger to write to stdout. Safe to call more than once."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(KeyValueFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
