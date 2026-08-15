"""The pre-Hunter gate: spend no credit on a firm we already have a contact for.

Hunter credits are finite. Looking up a campus recruiting contact at a firm we
already have a relationship record for, whose contact we already have and have
not flagged stale, buys nothing. That is true of a current partner and equally
true of a lapsed one: a win-back goes to the person who ran the last contract,
not to whoever Hunter surfaces today. The gate skips the whole discovery path
for exactly that case:

    contact_status IN (existing_partner, lapsed_partner)
    AND has_known_contact == TRUE
    AND contact_needs_refresh == FALSE

Any other combination proceeds to Hunter as normal. Two cases matter in
practice: a lapsed partner flagged contact_needs_refresh proceeds, because a
contact who has gone quiet since the contract ended is the one case where
spending a credit is worth it; and a missing or unrecognized contact_status does
NOT skip, because a gate that fails closed on bad data would silently stop
discovering contacts.

The two skip reasons are worded differently on purpose, so existing_partner and
lapsed_partner skips stay distinguishable in contacts/hunter_usage.csv.

All three values come from data/targets.csv, which is the only input. The CRM is
never read at runtime; scripts/derive_target_status.py puts those columns there
as a separate manual step.

Every skip is logged with its reason and all three field values, and recorded in
contacts/hunter_usage.csv as a zero-credit line, so the audit trail shows what
was not spent as well as what was.

Usage:
    from contacts.gate import evaluate_pre_hunter_gate
    decision = evaluate_pre_hunter_gate(target)
    if decision.skip:
        return []
"""

from dataclasses import dataclass
from typing import Any, Mapping

from common.logging import get_logger

log = get_logger("contacts.gate")

EXISTING_PARTNER = "existing_partner"
LAPSED_PARTNER = "lapsed_partner"

# Statuses that imply a relationship record, and therefore a contact worth
# reusing rather than re-discovering.
SKIPPABLE_STATUSES = (EXISTING_PARTNER, LAPSED_PARTNER)

# Distinct wording per status. These strings land in hunter_usage.csv, which is
# read later to answer why a firm was never looked up.
SKIP_REASONS = {
    EXISTING_PARTNER: (
        "skipped: existing partner with a known contact that is not flagged for "
        "refresh, so no credit is spent"
    ),
    LAPSED_PARTNER: (
        "skipped: lapsed partner with a known contact that is not flagged for "
        "refresh; a win-back goes to the contact on file, so no credit is spent"
    ),
}

TRUE_VALUES = {"true", "yes", "y", "1"}


def as_bool(value: Any) -> bool:
    """Read a CSV boolean. Anything unrecognized is False."""
    return str(value or "").strip().lower() in TRUE_VALUES


@dataclass(frozen=True)
class GateDecision:
    """Whether to call Hunter for this firm, and the three values behind it."""
    skip: bool
    reason: str
    firm: str
    contact_status: str
    has_known_contact: bool
    contact_needs_refresh: bool

    def describe(self) -> str:
        return (
            f"{self.reason} "
            f"[contact_status={self.contact_status or 'missing'}, "
            f"has_known_contact={str(self.has_known_contact).upper()}, "
            f"contact_needs_refresh={str(self.contact_needs_refresh).upper()}]"
        )


def evaluate_pre_hunter_gate(target: Mapping[str, Any]) -> GateDecision:
    """Decide whether this target's Hunter lookup can be skipped outright."""
    status = str(target.get("contact_status") or "").strip()
    known = as_bool(target.get("has_known_contact"))
    refresh = as_bool(target.get("contact_needs_refresh"))
    firm = str(target.get("firm") or "")

    if status in SKIPPABLE_STATUSES and known and not refresh:
        reason = SKIP_REASONS[status]
        skip = True
    elif status in SKIPPABLE_STATUSES and known and refresh:
        reason = (f"proceeding: {status.replace('_', ' ')}, but the contact on file "
                  "is flagged for refresh")
        skip = False
    elif status in SKIPPABLE_STATUSES:
        reason = f"proceeding: {status.replace('_', ' ')} with no known contact on file"
        skip = False
    elif not status:
        reason = ("proceeding: no contact_status in targets.csv, so the gate cannot "
                  "claim a contact is known. Run scripts/derive_target_status.py")
        skip = False
    else:
        reason = f"proceeding: contact_status is {status}"
        skip = False

    return GateDecision(
        skip=skip, reason=reason, firm=firm, contact_status=status,
        has_known_contact=known, contact_needs_refresh=refresh,
    )


def log_skip(decision: GateDecision, domain: str = "") -> None:
    """Log a skip and record a zero-credit line in the Hunter usage log.

    The usage log is the record of what Hunter cost. A skip belongs in it for the
    same reason a call does: so the file answers "where did the credits go" and
    "where did they not go" from one place.
    """
    log.info("Pre-Hunter gate for %s: %s", decision.firm, decision.describe())

    try:
        from contacts.hunter_guard import HunterGuard

        guard = HunterGuard.from_settings()
        guard.record_skip("domain-search", domain, note=f"SKIPPED: {decision.describe()}")
    except Exception as exc:  # pragma: no cover - auditing must never block the run
        log.debug("Could not record the skip in the usage log: %s", exc)
