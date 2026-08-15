"""Audit-recency, dedup, and free-text eligibility helpers for the 2026 audit data.

The BLK Undergraduate Audit spreadsheet has no per-sponsor rating columns —
graduation year is a single free-text/numeric field, career interest is one
messy comma-separated free-text field, and students who re-submitted the
audit appear as multiple rows with the same BLK ID. These helpers isolate
that mess from the builders that consume the cleaned-up DataFrame.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.utils.logging import get_logger

log = get_logger("processing.audit_filter")


def filter_recent_and_dedupe(
    df: pd.DataFrame,
    timestamp_col: str,
    id_col: str,
    min_year: int = 2026,
) -> pd.DataFrame:
    """Keep only rows timestamped *min_year* or later, then dedupe by *id_col*.

    When a student submitted the audit more than once, the row with the
    latest timestamp wins.
    """
    working = df.copy()
    ts = pd.to_datetime(working[timestamp_col], errors="coerce")
    before = len(working)
    working = working[ts.dt.year >= min_year].copy()
    working["_ts_parsed"] = ts[ts.dt.year >= min_year]
    log.info(
        "Recency filter: kept %d/%d rows with %s year >= %d.",
        len(working), before, timestamp_col, min_year,
    )

    working = working.sort_values("_ts_parsed")
    deduped = working.drop_duplicates(subset=id_col, keep="last").drop(columns="_ts_parsed")
    dropped = len(working) - len(deduped)
    if dropped:
        log.info("Dedup: dropped %d duplicate submission(s) by '%s' (kept latest).", dropped, id_col)

    return deduped


def parse_graduation_year(
    row: pd.Series,
    primary_col: str,
    fallback_col: Optional[str] = None,
) -> Optional[int]:
    """Parse a graduation year from *primary_col*, falling back to *fallback_col*.

    Handles messy free-text values (e.g. "Dec 2027 possibly May 2028") by
    trying the fallback column, which is sometimes cleaner for the same row.
    """
    for col in (primary_col, fallback_col):
        if not col:
            continue
        raw = row.get(col)
        if raw is None or raw == "":
            continue
        try:
            return int(float(raw))
        except (ValueError, TypeError):
            continue
    return None


def matches_any_keyword(text: object, keywords: list[str]) -> bool:
    """Case-insensitive substring match of *text* against any of *keywords*.

    Matches against the raw string rather than splitting on commas, since
    some free-text values contain internal commas inside parentheses
    (e.g. "Research (Equity, Macro)") that break naive comma-splitting.
    """
    if not isinstance(text, str) or not text:
        return False
    low = text.lower()
    return any(k.lower() in low for k in keywords)
