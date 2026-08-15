"""Logging setup shared across the outreach pipeline.

Mirrors the resume book's convention: one named logger per module, configured
once at import.

Usage:
    from common.logging import get_logger
    log = get_logger("research.fetch")
"""

import logging
import os
import sys

_CONFIGURED = False


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = os.getenv("OUTREACH_LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)-7s %(name)-22s %(message)s"))
    root = logging.getLogger("outreach")
    root.setLevel(getattr(logging, level, logging.INFO))
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger namespaced under 'outreach'."""
    _configure()
    return logging.getLogger(f"outreach.{name}")
