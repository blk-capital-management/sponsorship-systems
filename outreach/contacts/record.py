"""The contact record: one person, plus how we found them.

The contact file is the only place contact_provenance exists. It records how a
name or address was discovered, which for Hunter is usually a Google search
scoped to LinkedIn. That is a true statement about discovery and a useless firm
claim, so it lives in its own namespace, is marked internal_only, and is refused
by every renderer (see common/namespaces.py).

The CSV keeps flat columns because a human reads it. `load_contacts` folds the
contact_provenance_* columns back into one flagged object, so anything
downstream that touches provenance touches a structure the render guard can
recognize.

Usage:
    from contacts.record import load_contacts
    contacts = load_contacts("balyasny")
    contacts[0].email, contacts[0].contact_provenance["discovery_url"]
"""

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.config import PROJECT_ROOT
from common.logging import get_logger
from common.namespaces import build_contact_provenance
from common.owners import normalize_owner

log = get_logger("contacts.record")

OUT_DIR = PROJECT_ROOT / "contacts" / "out"

# Columns written by contacts/discover.py.
CONTACT_FIELDS = [
    "owner", "name", "title", "title_rank", "email", "pattern", "pattern_confidence",
    "contact_provenance_pattern_url", "contact_provenance_discovery_url",
    "contact_provenance_internal_only",
]

# Columns written by contacts/verify.py, which adds the provider's verdict.
VERIFIED_FIELDS = [
    "owner", "name", "title", "title_rank", "email", "verification_score",
    "verification_status", "verification_provider", "pattern",
    "pattern_confidence", "contact_provenance_pattern_url",
    "contact_provenance_discovery_url", "contact_provenance_internal_only",
]
DROPPED_FIELDS = VERIFIED_FIELDS + ["drop_reason"]

# Pre-migration column names, read on load so an old file still opens.
_LEGACY_ALIASES = {
    "contact_provenance_discovery_url": "source_url",
    "contact_provenance_pattern_url": "pattern_source_url",
}


@dataclass
class ContactRecord:
    """One contact. `contact_provenance` never reaches a rendered email."""
    name: str
    owner: str
    title: str = ""
    email: str = ""
    title_rank: str = ""
    pattern: str = ""
    pattern_confidence: str = ""
    verification_score: str = ""
    verification_status: str = ""
    verification_provider: str = ""
    contact_provenance: dict[str, Any] = field(default_factory=build_contact_provenance)

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name.strip() else ""

    def renderable(self) -> dict[str, str]:
        """The half of the record a draft may see. Provenance is not in it."""
        return {
            "owner": self.owner,
            "name": self.name,
            "first_name": self.first_name,
            "title": self.title,
            "email": self.email,
        }


def _get(row: dict[str, Any], column: str) -> str:
    """Read a column, falling back to its pre-migration name."""
    value = row.get(column)
    if value in (None, ""):
        value = row.get(_LEGACY_ALIASES.get(column, ""), "")
    return (value or "").strip()


def to_contact_row(row: dict[str, Any], fields: list[str] | None = None) -> dict[str, str]:
    """Flatten an in-memory row to CSV columns, renaming into the namespace."""
    fields = fields or CONTACT_FIELDS
    flat = {key: row.get(key, "") for key in fields}
    flat["contact_provenance_discovery_url"] = _get(row, "contact_provenance_discovery_url")
    flat["contact_provenance_pattern_url"] = _get(row, "contact_provenance_pattern_url")
    flat["contact_provenance_internal_only"] = "TRUE"
    return {key: ("" if flat.get(key) is None else str(flat.get(key, ""))) for key in fields}


def from_row(row: dict[str, Any]) -> ContactRecord:
    """Build a ContactRecord from one CSV row, folding provenance into an object."""
    return ContactRecord(
        name=(row.get("name") or "").strip(),
        owner=(row.get("owner") or "").strip().lower(),
        title=(row.get("title") or "").strip(),
        email=(row.get("email") or "").strip(),
        title_rank=str(row.get("title_rank") or "").strip(),
        pattern=(row.get("pattern") or "").strip(),
        pattern_confidence=str(row.get("pattern_confidence") or "").strip(),
        verification_score=str(row.get("verification_score") or "").strip(),
        verification_status=(row.get("verification_status") or "").strip(),
        verification_provider=(row.get("verification_provider") or "").strip(),
        contact_provenance=build_contact_provenance(
            _get(row, "contact_provenance_discovery_url"),
            _get(row, "contact_provenance_pattern_url"),
            method="pattern_inference" if row.get("pattern") else "provider_published",
            provider=(row.get("verification_provider") or "").strip(),
        ),
    )


def load_contacts(slug: str, *, verified_only: bool = True,
                  out_dir: Path | None = None) -> list[ContactRecord]:
    """Load one firm's contacts. Verified file first, discovery file otherwise.

    Args:
        slug: firm_slug, the join key across every stage.
        verified_only: Read <slug>_verified.csv. False reads <slug>_contacts.csv.
        out_dir: Override the contacts/out directory, for tests.
    """
    directory = out_dir or OUT_DIR
    name = f"{slug}_verified.csv" if verified_only else f"{slug}_contacts.csv"
    path = directory / name
    if not path.exists():
        raise FileNotFoundError(
            f"No contact file at {path}. Run contacts/discover.py and "
            "contacts/verify.py for this firm first."
        )
    with path.open(newline="", encoding="utf-8") as fh:
        records = [from_row(row) for row in csv.DictReader(fh)]
    for record in records:
        record.owner = normalize_owner(record.owner)
    return records
