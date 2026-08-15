"""Structured error/warning collector for a single pipeline run.

Accumulates ErrorRecord instances during validation (and later during building),
then writes them to a timestamped CSV in output/reports/.

CSV columns:
  severity, category, row_number, student_name, graduation_year,
  sponsor, field, issue, recommended_fix
"""

import csv
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from src.utils.file_utils import ensure_dir, timestamped_filename
from src.utils.logging import get_logger

log = get_logger("reporting.error_report")

# ── Severity constants ────────────────────────────────────────────────────────
ERROR = "error"
WARNING = "warning"

# ── Category constants ────────────────────────────────────────────────────────
CAT_CONFIG = "config"
CAT_DATA_SOURCE = "data_source"
CAT_COLUMN_MAPPING = "column_mapping"
CAT_MISSING_FIELD = "missing_field"
CAT_INVALID_LINK = "invalid_link"
CAT_DUPLICATE = "duplicate"
CAT_GRAD_YEAR = "graduation_year"
CAT_TRANSITION_PAGE = "transition_page"
CAT_SPONSOR = "sponsor"

CSV_COLUMNS = [
    "severity",
    "category",
    "row_number",
    "student_name",
    "graduation_year",
    "sponsor",
    "field",
    "issue",
    "recommended_fix",
]


@dataclass
class ErrorRecord:
    severity: str                    # "error" or "warning"
    category: str                    # one of the CAT_* constants
    issue: str                       # human-readable description
    recommended_fix: str             # actionable instructions for the chair
    row_number: Optional[int] = None
    student_name: Optional[str] = None
    graduation_year: Optional[str] = None
    sponsor: Optional[str] = None
    field: Optional[str] = None      # internal field name (e.g. "resume_link")


class ErrorReportBuilder:
    """Collects ErrorRecord instances and writes a CSV at end of run."""

    def __init__(self) -> None:
        self._records: list[ErrorRecord] = []

    # ── Add helpers ──────────────────────────────────────────────────────────

    def add(self, record: ErrorRecord) -> None:
        self._records.append(record)

    def error(self, category: str, issue: str, recommended_fix: str, **kwargs) -> None:
        self.add(ErrorRecord(severity=ERROR, category=category, issue=issue,
                             recommended_fix=recommended_fix, **kwargs))

    def warning(self, category: str, issue: str, recommended_fix: str, **kwargs) -> None:
        self.add(ErrorRecord(severity=WARNING, category=category, issue=issue,
                             recommended_fix=recommended_fix, **kwargs))

    # ── Counts ───────────────────────────────────────────────────────────────

    @property
    def error_count(self) -> int:
        return sum(1 for r in self._records if r.severity == ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for r in self._records if r.severity == WARNING)

    @property
    def records(self) -> list[ErrorRecord]:
        return list(self._records)

    # ── Write ────────────────────────────────────────────────────────────────

    def write_csv(self, reports_dir: Path, prefix: str = "validation_errors") -> Path:
        """Write all collected records to a timestamped CSV. Returns the file path."""
        ensure_dir(reports_dir)
        filename = timestamped_filename(prefix, ".csv")
        out_path = reports_dir / filename

        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for rec in self._records:
                row = asdict(rec)
                # Ensure every CSV column is present (dataclass may have fewer keys)
                writer.writerow({col: row.get(col, "") or "" for col in CSV_COLUMNS})

        log.info(
            "Error report written: %s  (%d errors, %d warnings)",
            out_path, self.error_count, self.warning_count,
        )
        return out_path
