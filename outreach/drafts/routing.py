"""Route a target by contact_status before any copy is generated.

The cold template says "we would welcome the chance to begin a partnership".
Sending that to Sixth Street, who signed in 2026 and expires this September,
reads as if nobody at BLK knows who they are. So contact_status decides, at
draft time, whether a cold draft may exist at all:

    cold_prospect     generate the cold draft
    existing_partner  generate warm renewal when CRM-derived fields exist
    lapsed_partner    generate recovery when CRM-derived fields exist

There is no fallthrough. A missing, empty, or unrecognized status raises rather
than defaulting to cold, because defaulting to cold is the exact failure this
routing exists to prevent. Existing and lapsed partners are queued only when
the CRM-derived relationship content their template needs is absent.

Usage:
    from drafts.routing import route_target, DraftRoutingError
    route_target(target)          # returns a recognized status or raises
"""

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from common.config import PROJECT_ROOT
from common.logging import get_logger

log = get_logger("drafts.routing")

COLD_PROSPECT = "cold_prospect"
EXISTING_PARTNER = "existing_partner"
LAPSED_PARTNER = "lapsed_partner"

VALID_STATUSES = frozenset({COLD_PROSPECT, EXISTING_PARTNER, LAPSED_PARTNER})

REVIEW_DIR = PROJECT_ROOT / "review"
NEEDS_WARM_CSV = REVIEW_DIR / "needs_warm_template.csv"
NEEDS_RECOVERY_CSV = REVIEW_DIR / "needs_recovery_template.csv"

QUEUE_FIELDS = ["firm", "firm_slug", "domain", "contact_status",
                "has_known_contact", "missing_fields", "reason", "queued_at"]

# Which queue receives a non-cold target whose relationship record is incomplete.
QUEUE_BY_STATUS = {
    EXISTING_PARTNER: NEEDS_WARM_CSV,
    LAPSED_PARTNER: NEEDS_RECOVERY_CSV,
}

RELATIONSHIP_REQUIRED_FIELDS = (
    "relationship_tier",
    "relationship_status",
    "relationship_contact_name",
    "relationship_contact_email",
    "relationship_crm_source",
)
RECOVERY_REQUIRED_FIELDS = RELATIONSHIP_REQUIRED_FIELDS + (
    "relationship_decline_reason",
)


class DraftRoutingError(RuntimeError):
    """Raised when a target must not receive a cold draft."""

    def __init__(self, message: str, *, firm: str = "", contact_status: str = "",
                 queue_path: Path | None = None):
        super().__init__(message)
        self.firm = firm
        self.contact_status = contact_status
        self.queue_path = queue_path


def _append(path: Path, row: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=QUEUE_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in QUEUE_FIELDS})
    return path


def missing_relationship_fields(target: Mapping[str, Any], status: str) -> list[str]:
    """Return the CRM-derived fields required before a relationship draft may exist."""
    required = RECOVERY_REQUIRED_FIELDS if status == LAPSED_PARTNER else (
        RELATIONSHIP_REQUIRED_FIELDS if status == EXISTING_PARTNER else ()
    )
    return [name for name in required if not str(target.get(name) or "").strip()]


def route_target(target: Mapping[str, Any], *, slug: str = "",
                 review_dir: Path | None = None) -> str:
    """Return a recognized status when its required drafting data is present.

    Args:
        target: The row from data/targets.csv.
        slug: firm_slug, recorded in the queue row.
        review_dir: Override the review directory, for tests.

    Raises:
        DraftRoutingError: For incomplete relationship content or a status that
            is missing or unrecognized.
    """
    firm = str(target.get("firm") or "")
    status = str(target.get("contact_status") or "").strip()

    if status == COLD_PROSPECT:
        log.info("%s: cold_prospect, generating a cold draft.", firm)
        return COLD_PROSPECT

    if status in QUEUE_BY_STATUS:
        missing = missing_relationship_fields(target, status)
        if not missing:
            log.info("%s: %s with complete CRM-derived relationship content.", firm, status)
            return status

        default_path = QUEUE_BY_STATUS[status]
        reason = "missing CRM-derived relationship content required by the template"
        path = (review_dir / default_path.name) if review_dir else default_path
        _append(path, {
            "firm": firm,
            "firm_slug": slug,
            "domain": target.get("domain", ""),
            "contact_status": status,
            "has_known_contact": target.get("has_known_contact", ""),
            "missing_fields": "; ".join(missing),
            "reason": reason,
            "queued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        log.warning("%s: %s. No draft generated. Queued in %s.",
                    firm, status, path.name)
        raise DraftRoutingError(
            f"{firm} is {status}, but {reason}: {', '.join(missing)}. No draft "
            f"was generated; the firm is queued in {path.name}.",
            firm=firm, contact_status=status, queue_path=path,
        )

    # Neither cold nor a known non-cold status. Treating this as cold is exactly
    # the silent fallthrough this function exists to prevent.
    raise DraftRoutingError(
        f"{firm} has contact_status {status or '(empty)'!r}, which is not one of "
        f"{', '.join(sorted(VALID_STATUSES))}. Nothing is assumed to be cold. Run "
        "scripts/derive_target_status.py to populate data/targets.csv.",
        firm=firm, contact_status=status,
    )
