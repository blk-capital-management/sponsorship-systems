"""Write a one-row run summary CSV after each validate / build run.

CSV columns:
  run_timestamp, command, data_source_type, total_rows, valid_rows,
  error_count, warning_count, unique_grad_years_detected,
  sponsor_columns_detected, output_error_report_path,
  output_summary_path, validation_passed
"""

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.utils.file_utils import ensure_dir, timestamped_filename
from src.utils.logging import get_logger

log = get_logger("reporting.run_summary")

CSV_COLUMNS = [
    "run_timestamp",
    "command",
    "data_source_type",
    "total_rows",
    "valid_rows",
    "error_count",
    "warning_count",
    "unique_grad_years_detected",
    "sponsor_columns_detected",
    # build-general fields
    "build_included_count",
    "build_skipped_count",
    "build_output_path",
    # build-sponsors fields
    "sponsors_built",
    "sponsors_failed",
    # shared
    "output_error_report_path",
    "output_summary_path",
    "validation_passed",
]


@dataclass
class RunSummary:
    command: str
    data_source_type: str                   # "excel" or "sheets"
    total_rows: int = 0
    valid_rows: int = 0
    error_count: int = 0
    warning_count: int = 0
    unique_grad_years_detected: int = 0
    sponsor_columns_detected: int = 0
    # build-general
    build_included_count: int = 0
    build_skipped_count: int = 0
    build_output_path: str = ""
    # build-sponsors
    sponsors_built: int = 0
    sponsors_failed: int = 0
    # shared
    output_error_report_path: str = ""
    output_summary_path: str = ""
    validation_passed: bool = False
    run_timestamp: str = ""

    def __post_init__(self):
        if not self.run_timestamp:
            self.run_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_run_summary(summary: RunSummary, reports_dir: Path) -> Path:
    """Write the RunSummary to a timestamped CSV. Returns the file path."""
    ensure_dir(reports_dir)
    filename = timestamped_filename("validation_summary", ".csv")
    out_path = reports_dir / filename

    # Back-fill the summary path into the row itself
    summary.output_summary_path = str(out_path)

    row = {
        "run_timestamp": summary.run_timestamp,
        "command": summary.command,
        "data_source_type": summary.data_source_type,
        "total_rows": summary.total_rows,
        "valid_rows": summary.valid_rows,
        "error_count": summary.error_count,
        "warning_count": summary.warning_count,
        "unique_grad_years_detected": summary.unique_grad_years_detected,
        "sponsor_columns_detected": summary.sponsor_columns_detected,
        "build_included_count": summary.build_included_count,
        "build_skipped_count": summary.build_skipped_count,
        "build_output_path": summary.build_output_path,
        "sponsors_built": summary.sponsors_built,
        "sponsors_failed": summary.sponsors_failed,
        "output_error_report_path": summary.output_error_report_path,
        "output_summary_path": summary.output_summary_path,
        "validation_passed": summary.validation_passed,
    }

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerow(row)

    log.info("Run summary written: %s", out_path)
    return out_path
