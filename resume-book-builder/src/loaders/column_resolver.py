"""Resolve spreadsheet column headers to internal field names using column_mapping.yaml.

This replaces the fragile find_col() / find_column_by_keywords() heuristics in the
legacy scripts with a config-driven alias lookup.
"""

from typing import Optional
from src.utils.logging import get_logger

log = get_logger("loaders.column_resolver")

# Internal field names used throughout the codebase.
REQUIRED_FIELDS = ("first_name", "last_name", "graduation_year", "resume_link")


def resolve_columns(df_columns: list[str], mapping: dict) -> dict[str, Optional[str]]:
    """Match each internal field to an actual column header in df_columns.

    Args:
        df_columns: List of actual column headers from the DataFrame.
        mapping: Parsed column_mapping.yaml['fields'] dict.

    Returns:
        Dict mapping internal field name → matched column header (or None if unresolved).
    """
    cols_lower = {c.lower(): c for c in df_columns}
    resolved: dict[str, Optional[str]] = {}

    for field, aliases in mapping.get("fields", {}).items():
        match = None
        for alias in aliases:
            actual = cols_lower.get(alias.lower())
            if actual is not None:
                match = actual
                log.debug("Field '%s' resolved to column '%s'", field, actual)
                break
        if match is None:
            log.warning("Field '%s' not found in columns. Tried: %s", field, aliases)
        resolved[field] = match

    return resolved


def assert_required_fields(resolved: dict[str, Optional[str]]) -> list[str]:
    """Return a list of required fields that could not be resolved (empty = all good)."""
    return [f for f in REQUIRED_FIELDS if not resolved.get(f)]
