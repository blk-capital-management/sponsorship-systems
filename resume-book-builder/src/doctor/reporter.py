"""Print and write the doctor report."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from src.doctor.checks import DoctorResult

_COLORS = {
    "PASS": "\033[92m",    # green
    "WARNING": "\033[93m", # yellow
    "ERROR": "\033[91m",   # red
    "RESET": "\033[0m",
}

_SYMBOLS = {"PASS": "✓", "WARNING": "!", "ERROR": "✗"}


def _colorize(status: str, text: str) -> str:
    return f"{_COLORS.get(status, '')}{text}{_COLORS['RESET']}"


def print_report(results: list[DoctorResult]) -> None:
    print()
    print("=" * 70)
    print("  BLK Resume Book Builder — Setup Doctor")
    print("=" * 70)

    for r in results:
        sym = _SYMBOLS.get(r.status, "?")
        label = f"[{r.status}]".ljust(9)
        colored = _colorize(r.status, f"{sym} {label}")
        print(f"  {colored}  {r.check}")
        print(f"            {r.detail}")
        if r.fix:
            print(f"            → FIX: {r.fix}")
        print()

    errors = [r for r in results if r.status == "ERROR"]
    warnings = [r for r in results if r.status == "WARNING"]
    passes = [r for r in results if r.status == "PASS"]

    print("─" * 70)
    print(f"  {_colorize('PASS', f'{len(passes)} passed')}  "
          f"{_colorize('WARNING', f'{len(warnings)} warnings')}  "
          f"{_colorize('ERROR', f'{len(errors)} errors')}")
    print("─" * 70)

    if errors:
        print(f"\n  {_colorize('ERROR', 'Errors must be resolved before the build will succeed.')}")
    elif warnings:
        print(f"\n  {_colorize('WARNING', 'Warnings are non-blocking but should be reviewed.')}")
    else:
        print(f"\n  {_colorize('PASS', 'All checks passed — ready to build!')}")
    print()


def write_csv_report(results: list[DoctorResult], reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = reports_dir / f"doctor_report_{ts}.csv"

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["status", "check", "detail", "fix"])
        writer.writeheader()
        for r in results:
            writer.writerow({"status": r.status, "check": r.check,
                             "detail": r.detail, "fix": r.fix})

    print(f"  Doctor report written: {out_path}")
    return out_path
