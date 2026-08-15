"""Logging configuration for the resume book builder."""

import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_logging(level: str = "INFO", log_to_file: bool = True, reports_dir: Path | None = None) -> logging.Logger:
    """Configure root logger with a human-readable console handler and optional file handler.

    Returns the root 'blk' logger. All sub-module loggers should use
    logging.getLogger('blk.<module>') so they inherit this config.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    logger = logging.getLogger("blk")
    logger.setLevel(logging.DEBUG)  # capture everything; handlers filter

    if logger.handlers:
        return logger  # already configured (e.g., called twice)

    console_fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(console_fmt)
    logger.addHandler(console_handler)

    if log_to_file and reports_dir is not None:
        reports_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = reports_dir / f"debug_{timestamp}.log"
        file_fmt = logging.Formatter(
            fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_fmt)
        logger.addHandler(file_handler)
        logger.debug("Log file: %s", log_path)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the 'blk' namespace."""
    return logging.getLogger(f"blk.{name}")
