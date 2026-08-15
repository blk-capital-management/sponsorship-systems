"""Preflight gate for draft generation.

Rule 5: BLK statistics come from config/blk_facts.json and nowhere else. A null
field there means the number is not confirmed, and an unconfirmed number must
never reach a sponsor's inbox. Preflight refuses to generate while any required
field is null and names the field that is blocking.

This runs at the top of every generation entry point. It deliberately does NOT
run at research time: research does not touch BLK's numbers, so a null
member_count should not block crawling.

Usage:
    from common.preflight import check_ready, PreflightError
    check_ready()   # raises PreflightError naming the first blocking field
"""

from typing import Any

from common.config import load_blk_facts, load_compliance
from common.logging import get_logger

log = get_logger("common.preflight")


class PreflightError(RuntimeError):
    """Raised when a required config field is null or empty."""


# Dotted paths into blk_facts.json that must be populated before any draft.
REQUIRED_BLK_FACTS = (
    "org_name",
    "year_descriptor",
    "applications_last_cycle",
    "member_count",
    "universities",
    "regions",
    "fall_conference.dates",
    "fall_conference.city",
    "fall_conference.host",
    "website",
    "placement_verticals",
)

# Dotted paths into compliance.json. The footer carries a reply-based opt-out
# and no physical mailing address, so nothing here depends on one.
REQUIRED_COMPLIANCE = (
    "opt_out_line",
    "uk_eu.legitimate_interest_basis",
)


def _dig(data: dict[str, Any], dotted: str) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, tuple, dict)) and len(value) == 0:
        return True
    return False


def missing_fields(
    facts: dict[str, Any] | None = None,
    compliance: dict[str, Any] | None = None,
) -> list[str]:
    """Return every blocking field as a dotted path, prefixed by its source file."""
    facts = load_blk_facts() if facts is None else facts
    compliance = load_compliance() if compliance is None else compliance

    blocking = [
        f"blk_facts.{path}" for path in REQUIRED_BLK_FACTS if _is_blank(_dig(facts, path))
    ]
    blocking += [
        f"compliance.{path}"
        for path in REQUIRED_COMPLIANCE
        if _is_blank(_dig(compliance, path))
    ]
    return blocking


def check_ready(
    facts: dict[str, Any] | None = None,
    compliance: dict[str, Any] | None = None,
) -> None:
    """Raise PreflightError if any required field is null or empty.

    The message names the blocking field so the fix is obvious. All blocking
    fields are listed, not just the first, so a run is not fixed one field at a
    time across three attempts.
    """
    blocking = missing_fields(facts, compliance)
    if not blocking:
        log.debug("Preflight passed.")
        return

    lines = "\n".join(f"  - {field}" for field in blocking)
    raise PreflightError(
        f"Draft generation blocked. {len(blocking)} required field(s) are not set:\n"
        f"{lines}\n"
        "Fill these in config/blk_facts.json and config/compliance.json, then rerun. "
        "Values are never inferred or invented (rule 5)."
    )
