"""Migrate Phase 2 and Phase 3 artifacts onto the split provenance namespaces.

One field name, `source_url`, used to mean two different things depending on
which file you were reading. This migration separates them:

  Research artifacts (Phase 2)
    alignment_hooks[].source_url  ->  alignment_hooks[].firm_claim_source
    (new) firm_claim_sources      =   distinct hook URLs, validated as direct
                                      URLs. A search page or a LinkedIn-scoped
                                      string fails the migration rather than
                                      being written and caught later.
    Evidence items keep `source_url`. It means "the firm page this text was read
    off", which is the same namespace, and the schema now applies the same
    direct-URL rules to it.

  Contact files (Phase 3)
    source_url          ->  contact_provenance_discovery_url
    pattern_source_url  ->  contact_provenance_pattern_url
    (new) contact_provenance_internal_only = TRUE

Fixtures are not hand-edited. Run this, read the diff, then run it with --apply.
The migration is idempotent: a second run reports nothing to do.

Usage:
    python scripts/migrate_provenance_namespaces.py            # diff only
    python scripts/migrate_provenance_namespaces.py --apply    # write
"""

import argparse
import csv
import difflib
import io
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.logging import get_logger  # noqa: E402
from common.namespaces import (  # noqa: E402
    ProvenanceNamespaceError,
    validate_firm_claim_source,
)
from contacts.record import (  # noqa: E402
    CONTACT_FIELDS, DROPPED_FIELDS, VERIFIED_FIELDS,
)

log = get_logger("scripts.migrate_provenance_namespaces")

RESEARCH_OUT = PROJECT_ROOT / "research" / "out"
CONTACTS_OUT = PROJECT_ROOT / "contacts" / "out"

# Which column set each contact file gets, by filename suffix.
CONTACT_FILE_FIELDS = {
    "_contacts.csv": CONTACT_FIELDS,
    "_verified.csv": VERIFIED_FIELDS,
    "_dropped.csv": DROPPED_FIELDS,
}

LEGACY_TO_NEW = {
    "source_url": "contact_provenance_discovery_url",
    "pattern_source_url": "contact_provenance_pattern_url",
}


class MigrationError(RuntimeError):
    """Raised when an artifact cannot be migrated without losing provenance."""


# ── Research artifacts ────────────────────────────────────────────────────────

def migrate_artifact(artifact: dict[str, Any], *, where: str) -> dict[str, Any]:
    """Return the artifact with hooks renamed and firm_claim_sources added.

    Raises:
        MigrationError: If a hook URL is not a valid firm claim source. That is
            a real finding, not a formatting problem, so it stops the migration.
    """
    migrated = dict(artifact)
    hooks: list[dict[str, Any]] = []
    sources: list[str] = []

    for index, hook in enumerate(artifact.get("alignment_hooks") or []):
        raw = (hook.get("firm_claim_source") or hook.get("source_url") or "").strip()
        try:
            url = validate_firm_claim_source(raw, where=f"{where} alignment_hooks[{index}]")
        except ProvenanceNamespaceError as exc:
            raise MigrationError(str(exc)) from exc
        new_hook = {k: v for k, v in hook.items() if k != "source_url"}
        new_hook["firm_claim_source"] = url
        # Key order matters only for the diff a human reads.
        hooks.append({
            "text": new_hook.get("text", ""),
            "firm_claim_source": url,
            "quote": new_hook.get("quote", ""),
            "basis": new_hook.get("basis", ""),
        })
        if url not in sources:
            sources.append(url)

    # Evidence URLs stay named source_url but must still be firm-namespace clean.
    for field_name, items in artifact.items():
        if not isinstance(items, list) or field_name in ("alignment_hooks", "pages_crawled",
                                                         "gaps", "firm_claim_sources"):
            continue
        for index, item in enumerate(items):
            if isinstance(item, dict) and "source_url" in item:
                try:
                    validate_firm_claim_source(
                        item["source_url"], where=f"{where} {field_name}[{index}].source_url"
                    )
                except ProvenanceNamespaceError as exc:
                    raise MigrationError(str(exc)) from exc

    migrated["alignment_hooks"] = hooks
    # Insert firm_claim_sources directly after alignment_hooks so the diff reads
    # in the same order as the schema.
    ordered: dict[str, Any] = {}
    for key, value in migrated.items():
        if key == "firm_claim_sources":
            continue
        ordered[key] = value
        if key == "alignment_hooks":
            ordered["firm_claim_sources"] = sources
    if "firm_claim_sources" not in ordered:
        ordered["firm_claim_sources"] = sources
    return ordered


def _render_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


# ── Contact files ─────────────────────────────────────────────────────────────

def migrate_contact_csv(text: str, fields: list[str]) -> str:
    """Rename the provenance columns and stamp internal_only on every row."""
    reader = csv.DictReader(io.StringIO(text))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\r\n")
    writer.writeheader()

    for row in reader:
        migrated = {key: (row.get(key) or "") for key in fields}
        for legacy, new in LEGACY_TO_NEW.items():
            if not migrated.get(new):
                migrated[new] = (row.get(legacy) or row.get(new) or "")
        migrated["contact_provenance_internal_only"] = "TRUE"
        writer.writerow(migrated)

    return buffer.getvalue()


# ── Driver ────────────────────────────────────────────────────────────────────

def _read_raw(path: Path) -> str:
    """Read without newline translation.

    Path.read_text folds CRLF to LF, which would make every CSV line look
    changed against the csv module's CRLF output and defeat idempotence.
    """
    with path.open(encoding="utf-8", newline="") as fh:
        return fh.read()


def _write_raw(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _diff(path: Path, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path.relative_to(PROJECT_ROOT)}",
        tofile=f"b/{path.relative_to(PROJECT_ROOT)}",
    ))


def plan() -> list[tuple[Path, str, str]]:
    """Compute every file change. Nothing is written here."""
    changes: list[tuple[Path, str, str]] = []

    for path in sorted(RESEARCH_OUT.glob("*.json")):
        before = _read_raw(path)
        artifact = json.loads(before)
        after = _render_json(migrate_artifact(artifact, where=path.name))
        if after != before:
            changes.append((path, before, after))

    for path in sorted(CONTACTS_OUT.glob("*.csv")):
        suffix = next((s for s in CONTACT_FILE_FIELDS if path.name.endswith(s)), None)
        if suffix is None:
            log.warning("Skipping %s: not a recognized contact file.", path.name)
            continue
        before = _read_raw(path)
        after = migrate_contact_csv(before, CONTACT_FILE_FIELDS[suffix])
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

    try:
        changes = plan()
    except MigrationError as exc:
        print(f"Migration stopped: {exc}", file=sys.stderr)
        return 2

    if not changes:
        print("Nothing to migrate. Every artifact is already on the split namespaces.")
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
