"""Normalize URLs already stored in Phase 2 and Phase 3 artifacts.

Two cosmetic problems, both of which reach a human reviewer:

  1. Scheme-default ports. The crawler stored `https://www.bamfunds.com:443/`,
     which is the same URL as `https://www.bamfunds.com/` and reads like machine
     output in an evidence block. Stripped from every URL in both namespaces.

  2. Percent-encoded spaces. Hunter's discovery strings arrive as
     `site:linkedin.com%20hannah%20dinardo`. Decoded to spaces in the
     contact_provenance_* columns ONLY. Firm claim sources keep their encoding,
     because a firm claim source has to stay clickable.

Nothing else is touched. No field is added, removed, or reordered, and the
migration is idempotent: a second run reports nothing to do.

Capture-time normalization now happens in research/fetch.py and
contacts/providers/hunter.py, so this is a one-time cleanup of existing files
rather than a step in the pipeline.

Usage:
    python scripts/normalize_urls.py            # diff only
    python scripts/normalize_urls.py --apply    # write
"""

import argparse
import csv
import io
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.logging import get_logger  # noqa: E402
from common.namespaces import normalize_contact_url, strip_default_port  # noqa: E402
from scripts.migrate_provenance_namespaces import _diff, _read_raw, _write_raw  # noqa: E402

log = get_logger("scripts.normalize_urls")

RESEARCH_OUT = PROJECT_ROOT / "research" / "out"
CONTACTS_OUT = PROJECT_ROOT / "contacts" / "out"

# Keys in a research artifact that hold a URL. Anything not listed is left
# untouched, so this migration cannot quietly rewrite prose.
FIRM_URL_KEYS = ("url", "source_url", "firm_claim_source")

# The only columns where %20 is decoded.
CONTACT_URL_COLUMNS = (
    "contact_provenance_discovery_url",
    "contact_provenance_pattern_url",
)


def normalize_artifact(value: Any, key: str | None = None) -> Any:
    """Walk an artifact, normalizing only known URL fields."""
    if isinstance(value, dict):
        return {k: normalize_artifact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_artifact(item, key) for item in value]
    if isinstance(value, str) and (key in FIRM_URL_KEYS or key == "firm_claim_sources"):
        return strip_default_port(value)
    return value


def normalize_contact_csv(text: str) -> str:
    """Normalize the contact_provenance_* columns and nothing else."""
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\r\n")
    writer.writeheader()
    for row in reader:
        out = dict(row)
        for column in CONTACT_URL_COLUMNS:
            if out.get(column):
                out[column] = normalize_contact_url(out[column])
        writer.writerow(out)
    return buffer.getvalue()


def plan() -> list[tuple[Path, str, str]]:
    changes: list[tuple[Path, str, str]] = []

    for path in sorted(RESEARCH_OUT.glob("*.json")):
        before = _read_raw(path)
        artifact = json.loads(before)
        # firm_claim_sources is a bare list of strings, so its items arrive with
        # the parent key rather than a URL key of their own.
        artifact["firm_claim_sources"] = [
            strip_default_port(u) for u in artifact.get("firm_claim_sources", [])
        ]
        after = json.dumps(normalize_artifact(artifact), indent=2, ensure_ascii=False) + "\n"
        if after != before:
            changes.append((path, before, after))

    for path in sorted(CONTACTS_OUT.glob("*.csv")):
        before = _read_raw(path)
        after = normalize_contact_csv(before)
        if after != before:
            changes.append((path, before, after))

    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true",
                        help="Write the changes. Without it, only the diff is printed.")
    args = parser.parse_args(argv)

    changes = plan()
    if not changes:
        print("Nothing to normalize. Every stored URL is already clean.")
        return 0

    for path, before, after in changes:
        print(_diff(path, before, after), end="")

    print(f"\n{len(changes)} file(s) would change.")
    if not args.apply:
        print("Dry run. Re-run with --apply to write.")
        return 0

    for path, _before, after in changes:
        _write_raw(path, after)
    print(f"Wrote {len(changes)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
