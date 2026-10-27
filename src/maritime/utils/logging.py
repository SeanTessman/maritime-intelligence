"""Logging setup shared by CLI scripts, the API and the dashboard."""

from __future__ import annotations

import logging

_CONFIGURED = False


def setup_logging(level: str | None = None) -> None:
    """Configure root logging once with a consistent, grep-friendly format."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    from maritime.config import get_settings

    logging.basicConfig(
        level=(level or get_settings().miadp_log_level).upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    _CONFIGURED = True
