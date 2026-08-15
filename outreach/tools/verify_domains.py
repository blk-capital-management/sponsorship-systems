"""Audit every domain in targets.csv against the firm it claims to belong to.

The domains in targets.csv were resolved by hand. bam.com looked exactly right
for Balyasny Asset Management and belongs to a Dutch construction company, so
hand-entered input data deserves the same identity check the crawler applies to
its own output.

Anything that fails, or that cannot be checked, is written to
review/domain_review.csv for a human to confirm. Nothing is auto-corrected:
guessing a replacement domain would reintroduce exactly the problem this closes.

Usage:
    python tools/verify_domains.py
    python tools/verify_domains.py --refresh          # bypass the HTML cache
    python tools/verify_domains.py --firm "Citadel"   # check one row
"""

import argparse
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.config import load_settings, resolve_path  # noqa: E402
from common.http import Fetcher  # noqa: E402
from common.identity import (  # noqa: E402
    distinctive_tokens,
    identify_page,
    looks_bot_blocked,
    page_identity_text,
)
from common.logging import get_logger  # noqa: E402
from research.fetch import fetch_landing, load_targets  # noqa: E402

log = get_logger("tools.verify_domains")

STATUS_OK = "ok"
STATUS_MISMATCH = "mismatch"
STATUS_BOT_BLOCKED = "bot_blocked"
STATUS_UNREACHABLE = "unreachable"
STATUS_UNCHECKABLE = "uncheckable"

# Only a mismatch means the CSV is wrong. The rest mean "a human has to look",
# which is a different and much less alarming instruction.
NEEDS_DATA_FIX = {STATUS_MISMATCH}


def check_domain(fetcher: Fetcher, firm: str, domain: str, refresh: bool) -> dict[str, str]:
    """Return one audit row for a single firm and domain."""
    landing = fetch_landing(fetcher, domain, refresh)

    if not landing.ok:
        # A site declining automated access is not a wrong domain. Reporting it
        # as one would send someone hunting for a replacement that does not exist.
        blocked = looks_bot_blocked(landing.html, landing.status)
        return {
            "firm": firm, "domain": domain,
            "status": STATUS_BOT_BLOCKED if blocked else STATUS_UNREACHABLE,
            "presents_as": "", "resolved_url": landing.url,
            "detail": (
                f"bot protection declined automated access (HTTP {landing.status}). "
                "Domain is probably correct. Research this firm manually."
                if blocked else
                f"could not load the landing page ({landing.error or landing.status})"
            ),
        }

    presents_as = page_identity_text(landing.html, "").strip()[:100]

    if not distinctive_tokens(firm):
        return {
            "firm": firm, "domain": domain, "status": STATUS_UNCHECKABLE,
            "presents_as": presents_as, "resolved_url": landing.url,
            "detail": "firm name has no distinctive token to test; confirm by hand",
        }

    reason = identify_page(firm, domain, landing.html, landing.status)
    if reason:
        blocked = looks_bot_blocked(landing.html, landing.status)
        return {
            "firm": firm, "domain": domain,
            "status": STATUS_BOT_BLOCKED if blocked else STATUS_MISMATCH,
            "presents_as": presents_as, "resolved_url": landing.url,
            "detail": reason,
        }

    return {
        "firm": firm, "domain": domain, "status": STATUS_OK,
        "presents_as": presents_as, "resolved_url": landing.url, "detail": "",
    }


def write_review(rows: list[dict[str, str]], path: Path) -> None:
    """Write only the rows a human needs to look at."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flagged = [r for r in rows if r["status"] != STATUS_OK]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["firm", "domain", "status", "presents_as", "resolved_url", "detail"],
        )
        writer.writeheader()
        writer.writerows(flagged)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--firm", help="Check a single firm instead of all of them")
    parser.add_argument("--refresh", action="store_true", help="Bypass the HTML cache")
    args = parser.parse_args(argv)

    settings = load_settings()
    fetcher = Fetcher(settings["research"])

    targets = load_targets()
    if args.firm:
        targets = [t for t in targets if t["firm"].lower() == args.firm.lower()]
        if not targets:
            log.error("Firm %r is not in targets.csv.", args.firm)
            return 1

    rows = []
    for target in targets:
        row = check_domain(fetcher, target["firm"], target["domain"].strip(), args.refresh)
        rows.append(row)
        marker = "  " if row["status"] == STATUS_OK else "!!"
        log.info("%s %-32s %-28s %s", marker, row["firm"][:32],
                 row["domain"][:28], row["status"])

    review_path = resolve_path(settings["review"]["queue_path"]).parent / "domain_review.csv"
    if args.firm:
        # A single-firm spot check must not overwrite the full audit and silently
        # drop every other flagged row.
        log.info("Single-firm check. %s left unchanged.", review_path.name)
    else:
        write_review(rows, review_path)

    statuses = (STATUS_OK, STATUS_MISMATCH, STATUS_BOT_BLOCKED,
                STATUS_UNREACHABLE, STATUS_UNCHECKABLE)
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in statuses}
    log.info(
        "Checked %s domains: %s ok, %s mismatch, %s bot blocked, %s unreachable, "
        "%s uncheckable",
        len(rows), *(counts[s] for s in statuses),
    )
    if counts[STATUS_OK] != len(rows):
        log.warning("Rows needing review: %s", review_path)

    # Only a mismatch means targets.csv is wrong. Bot-blocked and unreachable
    # sites are reported for manual handling without failing the run.
    return 1 if counts[STATUS_MISMATCH] else 0


if __name__ == "__main__":
    raise SystemExit(main())
