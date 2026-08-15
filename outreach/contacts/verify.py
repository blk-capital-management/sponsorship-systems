"""Verify discovered addresses and drop anything below the threshold.

Dropping, not flagging. A flagged-but-present row is a row someone approves at
speed on a Friday, and undeliverable sends damage domain reputation, which
degrades deliverability on live sponsor threads.

Rows that survive keep their verification score so a human can see how strong
each one is. Rows that fail are written to contacts/out/<slug>_dropped.csv so
the drop is auditable rather than silent.

Usage:
    python contacts/verify.py --firm "Balyasny Asset Management"
    python contacts/verify.py --all
"""

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.config import PROJECT_ROOT as CFG_ROOT, load_settings  # noqa: E402
from common.logging import get_logger  # noqa: E402
from common.owners import normalize_owner  # noqa: E402
from common.slugify import firm_slug  # noqa: E402
from contacts.providers import ProviderNotConfigured, build_provider  # noqa: E402
from research.fetch import find_target, load_targets  # noqa: E402

log = get_logger("contacts.verify")

OUT_DIR = CFG_ROOT / "contacts" / "out"

from contacts.record import (  # noqa: E402
    DROPPED_FIELDS, VERIFIED_FIELDS, to_contact_row,
)


def read_contacts(slug: str) -> list[dict[str, Any]]:
    path = OUT_DIR / f"{slug}_contacts.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"No contact file at {path}. Run contacts/discover.py first."
        )
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(to_contact_row(row, fields))


def verify_rows(
    target: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    provider: Any = None,
    settings: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Verify in-memory contact rows without choosing a storage backend."""
    settings = settings or load_settings()
    threshold = int(settings["contacts"]["verification"]["min_score"])
    owner = normalize_owner(target.get("owner"))
    normalized = [{**row, "owner": owner} for row in rows]
    if not normalized:
        return [], []

    provider = provider or build_provider(settings)
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for row in normalized:
        email = (row.get("email") or "").strip()
        if not email:
            dropped.append({**row, "verification_score": "", "verification_status": "",
                            "verification_provider": provider.name,
                            "drop_reason": "no address could be built from a sourced pattern"})
            continue

        result = provider.verify(email)
        enriched = {
            **row,
            "verification_score": result.score,
            "verification_status": result.status,
            "verification_provider": result.provider,
        }
        if result.error:
            dropped.append({**enriched,
                            "drop_reason": f"verification failed ({result.error})"})
        elif result.score < threshold:
            dropped.append({**enriched,
                            "drop_reason": f"score {result.score} below threshold {threshold}"})
        else:
            kept.append(enriched)
    return kept, dropped


def verify_firm(firm: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Verify one firm's contacts. Returns (kept, dropped)."""
    settings = load_settings()
    target = find_target(firm, load_targets())
    slug = firm_slug(target["firm"])

    rows = read_contacts(slug)
    threshold = int(settings["contacts"]["verification"]["min_score"])

    log.info("Verifying %s contact(s) for %s (threshold %s)",
             len(rows), target["firm"], threshold)

    if not rows:
        _write(OUT_DIR / f"{slug}_verified.csv", [], VERIFIED_FIELDS)
        _write(OUT_DIR / f"{slug}_dropped.csv", [], DROPPED_FIELDS)
        log.info("  Nothing to verify.")
        return [], []

    kept, dropped = verify_rows(target, rows, settings=settings)

    _write(OUT_DIR / f"{slug}_verified.csv", kept, VERIFIED_FIELDS)
    _write(OUT_DIR / f"{slug}_dropped.csv", dropped, DROPPED_FIELDS)
    log.info("  %s kept, %s dropped -> contacts/out/%s_verified.csv",
             len(kept), len(dropped), slug)
    return kept, dropped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--firm", help="Firm name as it appears in data/targets.csv")
    group.add_argument("--all", action="store_true", help="Every target")
    parser.add_argument("--owner", help="With --all, restrict to one owner")
    args = parser.parse_args(argv)

    if args.firm:
        firms = [args.firm]
    else:
        targets = load_targets()
        if args.owner:
            targets = [t for t in targets
                       if t.get("owner", "").lower() == args.owner.lower()]
        firms = [t["firm"] for t in targets]

    failures = 0
    for name in firms:
        try:
            verify_firm(name)
        except ProviderNotConfigured as exc:
            log.error("%s", exc)
            return 2
        except Exception as exc:
            failures += 1
            log.error("Verification failed for %s: %s: %s", name, type(exc).__name__, exc)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
