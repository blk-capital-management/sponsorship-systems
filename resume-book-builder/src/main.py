"""BLK Capital Management — Resume Book Builder CLI.

Usage:
    python -m src.main doctor         --config config/settings.yaml
    python -m src.main validate       --config config/settings.yaml
    python -m src.main build-general  --config config/settings.yaml
    python -m src.main build-sponsors --config config/settings.yaml
    python -m src.main build-wells-fargo --config config/settings.yaml
    python -m src.main build-franklin-templeton --config config/settings.yaml
    python -m src.main compress-only  --input output/general/book.pdf --tier email_quality
"""

import argparse
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.utils.logging import setup_logging, get_logger


def load_settings(config_path: Path) -> dict:
    """Parse settings.yaml and return the config dict."""
    if not config_path.exists():
        print(f"ERROR: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)
    with config_path.open() as f:
        return yaml.safe_load(f) or {}


def load_column_mapping(config_dir: Path) -> dict:
    path = config_dir / "column_mapping.yaml"
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_sponsors_config(config_dir: Path) -> dict:
    path = config_dir / "sponsors.yaml"
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


# ── Subcommand handlers ───────────────────────────────────────────────────────

def cmd_doctor(args: argparse.Namespace, settings: dict, config_dir: Path) -> int:
    """Run setup-health checks without requiring real member data."""
    from src.doctor.checks import run_all_checks
    from src.doctor.reporter import print_report, write_csv_report

    output_cfg = settings.get("output", {})
    reports_dir = Path(output_cfg.get("reports_dir", "output/reports"))

    results = run_all_checks(settings, config_dir)
    print_report(results)
    write_csv_report(results, reports_dir)

    errors = [r for r in results if r.status == "ERROR"]
    return 1 if errors else 0


def cmd_validate(args: argparse.Namespace, settings: dict, config_dir: Path) -> int:
    """Stage 2 — pre-flight validation."""
    from pathlib import Path as _Path
    from src.validation.validator import run_validation
    from src.reporting.error_report import ErrorReportBuilder
    from src.reporting.run_summary import RunSummary, write_run_summary

    log = get_logger("main")

    column_mapping = load_column_mapping(config_dir)
    sponsors_config = load_sponsors_config(config_dir)

    reporter = ErrorReportBuilder()

    stats = run_validation(
        settings=settings,
        config_dir=config_dir,
        column_mapping=column_mapping,
        sponsors_config=sponsors_config,
        reporter=reporter,
    )

    output_cfg = settings.get("output", {})
    reports_dir = _Path(output_cfg.get("reports_dir", "output/reports"))

    # Write error report (always, even if empty)
    error_report_path = reporter.write_csv(reports_dir, prefix="validation_errors")

    # Determine pass/fail: errors = fail, warnings-only = pass
    validation_passed = reporter.error_count == 0

    summary = RunSummary(
        command="validate",
        data_source_type=stats.get("data_source_type", "excel"),
        total_rows=stats.get("total_rows", 0),
        valid_rows=stats.get("valid_rows", 0),
        error_count=reporter.error_count,
        warning_count=reporter.warning_count,
        unique_grad_years_detected=stats.get("unique_grad_years", 0),
        sponsor_columns_detected=stats.get("sponsor_columns_detected", 0),
        output_error_report_path=str(error_report_path),
        validation_passed=validation_passed,
    )
    summary_path = write_run_summary(summary, reports_dir)

    # ── Console summary ───────────────────────────────────────────────────────
    log.info("─" * 60)
    log.info("Validation complete")
    log.info("  Total rows checked : %d", summary.total_rows)
    log.info("  Valid rows         : %d", summary.valid_rows)
    log.info("  Errors             : %d", reporter.error_count)
    log.info("  Warnings           : %d", reporter.warning_count)
    log.info("  Result             : %s", "PASSED ✓" if validation_passed else "FAILED ✗")
    log.info("  Error report       : %s", error_report_path)
    log.info("  Run summary        : %s", summary_path)
    log.info("─" * 60)

    if not validation_passed:
        log.error(
            "Validation failed with %d error(s). Fix the issues listed in the error "
            "report before running build-general or build-sponsors.",
            reporter.error_count,
        )

    return 0 if validation_passed else 1


def cmd_build_general(args: argparse.Namespace, settings: dict, config_dir: Path) -> int:
    """Stage 3 — build the general BLK resume book."""
    from src.validation.validator import run_validation
    from src.reporting.error_report import ErrorReportBuilder
    from src.builders.general_builder import build_general

    log = get_logger("main")

    column_mapping = load_column_mapping(config_dir)
    sponsors_config = load_sponsors_config(config_dir)
    reporter = ErrorReportBuilder()

    stats = run_validation(
        settings=settings,
        config_dir=config_dir,
        column_mapping=column_mapping,
        sponsors_config=sponsors_config,
        reporter=reporter,
    )

    if reporter.error_count > 0:
        log.error(
            "Validation found %d error(s). Fix them before building. "
            "Run: python -m src.main validate --config %s",
            reporter.error_count, args.config,
        )
        return 1

    df = stats.get("df")
    resolved = stats.get("resolved", {})
    if df is None or df.empty:
        log.error("No data loaded; cannot build.")
        return 1

    result = build_general(settings=settings, config_dir=config_dir, df=df, resolved=resolved)

    log.info("─" * 60)
    log.info("build-general complete")
    log.info("  Members processed  : %d", result.total_members)
    log.info("  Resumes included   : %d", result.included_count)
    log.info("  Skipped            : %d", result.skipped_count)
    if result.output_path:
        log.info("  Output             : %s", result.output_path)
    if not result.success:
        log.error("Build failed: %s", result.error_detail)
    log.info("─" * 60)

    return 0 if result.success else 1


def cmd_build_sponsors(args: argparse.Namespace, settings: dict, config_dir: Path) -> int:
    """Stage 4 — build sponsor-specific resume books."""
    from src.validation.validator import run_validation
    from src.reporting.error_report import ErrorReportBuilder
    from src.builders.sponsor_builder import build_sponsors

    log = get_logger("main")

    column_mapping = load_column_mapping(config_dir)
    sponsors_config = load_sponsors_config(config_dir)
    reporter = ErrorReportBuilder()

    stats = run_validation(
        settings=settings,
        config_dir=config_dir,
        column_mapping=column_mapping,
        sponsors_config=sponsors_config,
        reporter=reporter,
    )

    if reporter.error_count > 0:
        log.error(
            "Validation found %d error(s). Fix them before building. "
            "Run: python -m src.main validate --config %s",
            reporter.error_count, args.config,
        )
        return 1

    df = stats.get("df")
    resolved = stats.get("resolved", {})
    if df is None or df.empty:
        log.error("No data loaded; cannot build.")
        return 1

    result = build_sponsors(
        settings=settings,
        config_dir=config_dir,
        df=df,
        resolved=resolved,
        sponsors_config=sponsors_config,
    )

    log.info("─" * 60)
    log.info("build-sponsors complete")
    for sr in result.sponsor_results:
        status = "OK" if sr.success else "FAILED"
        log.info(
            "  [%s] %-30s  %d/%d resumes%s",
            status, sr.sponsor_name, sr.included_count, sr.eligible_count,
            f"  → {sr.output_path.name}" if sr.output_path else "",
        )
        if sr.drive_link:
            log.info("        Drive: %s", sr.drive_link)
        if not sr.success and sr.error_detail:
            log.warning("        %s", sr.error_detail)
    log.info("─" * 60)

    return 0 if result.success else 1


def cmd_build_wells_fargo(args: argparse.Namespace, settings: dict, config_dir: Path) -> int:
    """Build the Wells Fargo resume book (Juniors + Seniors sections)."""
    from src.validation.validator import run_validation
    from src.reporting.error_report import ErrorReportBuilder
    from src.builders.wells_fargo_builder import build_wells_fargo

    log = get_logger("main")

    column_mapping = load_column_mapping(config_dir)
    sponsors_config = load_sponsors_config(config_dir)
    reporter = ErrorReportBuilder()

    stats = run_validation(
        settings=settings,
        config_dir=config_dir,
        column_mapping=column_mapping,
        sponsors_config=sponsors_config,
        reporter=reporter,
    )

    # Unlike build-general/build-sponsors, this command does not hard-block on
    # validation errors: the audit roster has known messy rows (missing/unparseable
    # graduation years for members outside the Wells Fargo grad-year cohorts) that
    # are irrelevant to this book's eligibility. Errors are still written to the
    # report for the roster owner to clean up later.
    if reporter.error_count > 0:
        log.warning(
            "Validation found %d error(s) (see error report). Proceeding anyway — "
            "none of these rows are eligible for the Wells Fargo book. "
            "Run: python -m src.main validate --config %s for details.",
            reporter.error_count, args.config,
        )

    df = stats.get("df")
    resolved = stats.get("resolved", {})
    if df is None or df.empty:
        log.error("No data loaded; cannot build.")
        return 1

    result = build_wells_fargo(settings=settings, config_dir=config_dir, df=df, resolved=resolved)

    log.info("─" * 60)
    log.info("build-wells-fargo complete")
    log.info(
        "  Juniors: %d/%d resumes included",
        result.junior.included_count, result.junior.eligible_count,
    )
    log.info(
        "  Seniors: %d/%d resumes included",
        result.senior.included_count, result.senior.eligible_count,
    )
    if result.output_path:
        log.info("  Output : %s", result.output_path)
    if not result.success:
        log.error("Build failed: %s", result.error_detail)
    log.info("─" * 60)

    return 0 if result.success else 1


def cmd_build_franklin_templeton(args: argparse.Namespace, settings: dict, config_dir: Path) -> int:
    """Build the Franklin Templeton resume book (class of 2029 only)."""
    from src.validation.validator import run_validation
    from src.reporting.error_report import ErrorReportBuilder
    from src.builders.franklin_templeton_builder import build_franklin_templeton

    log = get_logger("main")

    column_mapping = load_column_mapping(config_dir)
    sponsors_config = load_sponsors_config(config_dir)
    reporter = ErrorReportBuilder()

    stats = run_validation(
        settings=settings,
        config_dir=config_dir,
        column_mapping=column_mapping,
        sponsors_config=sponsors_config,
        reporter=reporter,
    )

    # Same rationale as build-wells-fargo: the audit roster has known messy rows
    # for members outside this book's cohort, which must not block the build.
    if reporter.error_count > 0:
        log.warning(
            "Validation found %d error(s) (see error report). Proceeding anyway — "
            "rows outside the class-of-2029 cohort are not eligible for this book. "
            "Run: python -m src.main validate --config %s for details.",
            reporter.error_count, args.config,
        )

    df = stats.get("df")
    resolved = stats.get("resolved", {})
    if df is None or df.empty:
        log.error("No data loaded; cannot build.")
        return 1

    result = build_franklin_templeton(
        settings=settings, config_dir=config_dir, df=df, resolved=resolved
    )

    log.info("─" * 60)
    log.info("build-franklin-templeton complete")
    log.info(
        "  Class of 2029: %d/%d resumes included (%d skipped)",
        result.included_count, result.eligible_count, result.skipped_count,
    )
    if result.output_path:
        log.info("  Output : %s", result.output_path)
    if not result.success:
        log.error("Build failed: %s", result.error_detail)
    log.info("─" * 60)

    return 0 if result.success else 1


def cmd_compress_only(args: argparse.Namespace, settings: dict, config_dir: Path) -> int:
    """Stage 5 — compress an existing PDF without rebuilding."""
    from src.processing.pdf_compressor import compress_pdf_ghostscript
    from src.utils.file_utils import file_size_mb

    log = get_logger("main")

    input_path: Path = args.input.resolve()
    tier: str = args.tier

    if not input_path.exists():
        log.error("Input file not found: %s", input_path)
        return 1

    log.info("Compressing %s (tier=%s)…", input_path, tier)
    pdf_bytes = input_path.read_bytes()
    compressed = compress_pdf_ghostscript(pdf_bytes, tier)

    if not compressed:
        log.error("Compression failed. Is Ghostscript installed?  brew install ghostscript")
        return 1

    out_path = input_path.parent / f"{input_path.stem}_compressed_{tier}.pdf"
    out_path.write_bytes(compressed)

    log.info("─" * 60)
    log.info("compress-only complete")
    log.info("  Input   : %s  (%.2f MB)", input_path, file_size_mb(input_path))
    log.info("  Output  : %s  (%.2f MB)", out_path, file_size_mb(out_path))
    log.info("  Tier    : %s", tier)
    log.info("─" * 60)

    return 0


# ── CLI wiring ────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.main",
        description="BLK Capital Management — Resume Book Builder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.main doctor         --config config/settings.yaml
  python -m src.main validate       --config config/settings.yaml
  python -m src.main build-general  --config config/settings.yaml
  python -m src.main build-sponsors --config config/settings.yaml
  python -m src.main build-wells-fargo --config config/settings.yaml
  python -m src.main build-franklin-templeton --config config/settings.yaml
  python -m src.main compress-only  --input output/general/book.pdf --tier email_quality
        """,
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    def _add_config(p):
        p.add_argument(
            "--config",
            type=Path,
            default=Path("config/settings.yaml"),
            metavar="PATH",
            help="Path to settings.yaml (default: config/settings.yaml)",
        )

    # doctor
    p_doctor = sub.add_parser(
        "doctor",
        help="Run setup health-checks (run this first before validate or build)",
    )
    _add_config(p_doctor)

    # validate
    p_validate = sub.add_parser(
        "validate",
        help="Check data quality before generating PDFs (produces error_report.csv)",
    )
    _add_config(p_validate)

    # build-general
    p_general = sub.add_parser(
        "build-general",
        help="Build the general BLK resume book for all members",
    )
    _add_config(p_general)

    # build-sponsors
    p_sponsors = sub.add_parser(
        "build-sponsors",
        help="Build sponsor-specific resume books (configured in config/sponsors.yaml)",
    )
    _add_config(p_sponsors)

    # build-wells-fargo
    p_wells_fargo = sub.add_parser(
        "build-wells-fargo",
        help="Build the Wells Fargo resume book (Juniors + Seniors sections)",
    )
    _add_config(p_wells_fargo)

    # build-franklin-templeton
    p_franklin = sub.add_parser(
        "build-franklin-templeton",
        help="Build the Franklin Templeton resume book (class of 2029 only)",
    )
    _add_config(p_franklin)

    # compress-only
    compress = sub.add_parser(
        "compress-only",
        help="Compress an existing PDF without rebuilding the full book",
    )
    _add_config(compress)
    compress.add_argument(
        "--input",
        type=Path,
        required=True,
        metavar="PDF",
        help="Path to the PDF to compress",
    )
    compress.add_argument(
        "--tier",
        choices=["high_quality", "email_quality", "small_size"],
        default="email_quality",
        help="Compression tier (default: email_quality)",
    )

    return parser


def main() -> int:
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    # args.config is defined on every subparser
    config_path = args.config.resolve()
    config_dir = config_path.parent
    settings = load_settings(config_path)

    # Set up logging from settings before doing anything else
    log_cfg = settings.get("logging", {})
    output_cfg = settings.get("output", {})
    reports_dir = Path(output_cfg.get("reports_dir", "output/reports"))
    setup_logging(
        level=log_cfg.get("level", "INFO"),
        log_to_file=log_cfg.get("log_to_file", True),
        reports_dir=reports_dir,
    )

    log = get_logger("main")
    log.info("BLK Resume Book Builder — command: %s", args.command)
    log.debug("Config: %s", config_path)

    dispatch = {
        "doctor": cmd_doctor,
        "validate": cmd_validate,
        "build-general": cmd_build_general,
        "build-sponsors": cmd_build_sponsors,
        "build-wells-fargo": cmd_build_wells_fargo,
        "build-franklin-templeton": cmd_build_franklin_templeton,
        "compress-only": cmd_compress_only,
    }

    handler = dispatch[args.command]
    return handler(args, settings, config_dir)


if __name__ == "__main__":
    sys.exit(main())
