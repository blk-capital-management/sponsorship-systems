"""Canonical CRM status vocabulary and effective-value helpers.

``targets`` remains BLK Bridge's one firm entity.  The database stores an
automatically derived value and an optional manual override for each CRM
dimension; these helpers are the single application-level definition of which
value wins and whether a firm belongs in the operational pipeline view.

The older ``contact_status`` field is deliberately retained.  Draft routing and
the pre-Hunter credit gate already depend on its three-value vocabulary, so it
acts as a compatibility projection of the effective relationship status rather
than a second CRM dimension.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping


RELATIONSHIP_STATUSES = (
    "Cold Prospect",
    "Existing Partner",
    "Global Partner",
    "Expired / Former Partner",
    "Not Renewing",
    "Not Interested",
    "Archived",
)

PIPELINE_STAGES = (
    "Researching",
    "Contact Ready",
    "Draft Ready",
    "Outreach Sent",
    "Follow-Up Due",
    "Responded",
    "Meeting Scheduled",
    "Re-engagement",
    "Renewal / In Conversation",
    "Proposal / Contract",
    "Stalled",
    "Closed / Partner",
    "Closed / No Active Workflow",
)

ACTIVE_PIPELINE_STAGES = frozenset({
    "Researching",
    "Contact Ready",
    "Draft Ready",
    "Outreach Sent",
    "Follow-Up Due",
    "Responded",
    "Meeting Scheduled",
    "Re-engagement",
    "Renewal / In Conversation",
    "Proposal / Contract",
    "Stalled",
})

TERMINAL_RELATIONSHIPS = frozenset({"Not Interested", "Archived"})

OUTREACH_NOT_SENT = "not_sent"
OUTREACH_AWAITING_RESPONSE = "awaiting_response"
OUTREACH_ADVANCED = "advanced"
OUTREACH_NOT_APPLICABLE = "not_applicable"

_OUTREACH_ADVANCEMENT_STAGES = frozenset({
    "Responded",
    "Meeting Scheduled",
    "Renewal / In Conversation",
    "Proposal / Contract",
    "Closed / Partner",
    "Closed / No Active Workflow",
})


def _date_value(value: Any) -> date | None:
    """Parse a database date without guessing incomplete or ambiguous values."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def effective_sponsorship_tier(
    target: Mapping[str, Any], *, today: date | None = None
) -> str | None:
    """Return a tier only while the firm has a future-dated agreement.

    ``relationship_expiration`` is the existing database field backing the
    product's expiration date. ``expiration_date`` is accepted as an API/data
    alias so callers do not need a second implementation of the lifecycle rule.
    """
    tier = str(target.get("sponsorship_tier") or "").strip()
    expiration = _date_value(
        target.get("expiration_date") or target.get("relationship_expiration")
    )
    if not tier or expiration is None or expiration <= (today or date.today()):
        return None
    return tier


def canonical_relationship(
    raw_status: Any = "", contact_status: Any = ""
) -> str:
    """Map the existing CRM/contact vocabulary without discarding distinctions."""
    raw = " ".join(str(raw_status or "").strip().lower().replace("_", " ").split())
    legacy = str(contact_status or "").strip().lower()

    if raw in {"cold prospect", "prospect", "new lead", "lead"}:
        return "Cold Prospect"
    if raw in {"global partner", "global sponsorship", "global"}:
        return "Global Partner"
    if raw in {"existing partner", "active", "renewed", "current", "partner"}:
        return "Existing Partner"
    if raw in {"not renewing", "non-renewing", "did not renew"}:
        return "Not Renewing"
    if raw in {"not interested", "declined", "do not contact"}:
        return "Not Interested"
    if raw in {"archived", "archive"}:
        return "Archived"
    if raw in {"expired", "lapsed", "former partner", "inactive", "churned", "lost"}:
        return "Expired / Former Partner"

    if legacy == "existing_partner":
        return "Existing Partner"
    if legacy == "lapsed_partner":
        return "Expired / Former Partner"
    return "Cold Prospect"


def canonical_pipeline_stage(
    raw_stage: Any = "",
    *,
    relationship_status: str = "Cold Prospect",
    notes: Any = "",
) -> str:
    """Normalize known workflow labels while preserving useful stage concepts."""
    raw = " ".join(str(raw_stage or "").strip().lower().replace("_", " ").split())
    note_text = str(notes or "").lower()

    if "re-engag" in raw or "re-engag" in note_text or "win-back" in note_text:
        return "Re-engagement"
    mapping = {
        "new lead": "Researching",
        "researching": "Researching",
        "research complete": "Contact Ready",
        "inherited warm lead": "Contact Ready",
        "contact ready": "Contact Ready",
        "draft ready": "Draft Ready",
        "draft generated": "Draft Ready",
        "outreach sent": "Outreach Sent",
        "sent": "Outreach Sent",
        "follow up": "Follow-Up Due",
        "follow-up": "Follow-Up Due",
        "follow-up due": "Follow-Up Due",
        "responded": "Responded",
        "1st call": "Meeting Scheduled",
        "meeting scheduled": "Meeting Scheduled",
        "renewal": "Renewal / In Conversation",
        "in convo": "Renewal / In Conversation",
        "in conversation": "Renewal / In Conversation",
        "proposal": "Proposal / Contract",
        "contract": "Proposal / Contract",
        "stalled": "Stalled",
        "closed": "Closed / Partner",
        "closed / partner": "Closed / Partner",
        "closed / no active workflow": "Closed / No Active Workflow",
    }
    if raw in mapping:
        return mapping[raw]
    if relationship_status in {"Global Partner"}:
        return "Closed / Partner"
    if relationship_status in TERMINAL_RELATIONSHIPS:
        return "Closed / No Active Workflow"
    if relationship_status == "Existing Partner":
        return "Closed / Partner"
    return "Researching"


def legacy_contact_status(relationship_status: str) -> str:
    """Project the canonical relationship into the existing drafting vocabulary."""
    if relationship_status in {"Existing Partner", "Global Partner"}:
        return "existing_partner"
    if relationship_status in {
        "Expired / Former Partner", "Not Renewing", "Not Interested", "Archived"
    }:
        return "lapsed_partner"
    return "cold_prospect"


def effective_relationship(target: Mapping[str, Any]) -> str:
    override = str(target.get("relationship_status_override") or "").strip()
    if override:
        return override
    automatic = str(target.get("relationship_status_auto") or "").strip()
    return automatic or canonical_relationship(
        target.get("relationship_status"), target.get("contact_status")
    )


def automatic_pipeline_stage(target: Mapping[str, Any]) -> str:
    automatic = str(target.get("pipeline_stage_auto") or "").strip()
    return automatic or canonical_pipeline_stage(
        target.get("crm_status"),
        relationship_status=effective_relationship(target),
        notes=target.get("notes"),
    )


def effective_pipeline_stage(target: Mapping[str, Any]) -> str:
    override = str(target.get("pipeline_stage_override") or "").strip()
    return override or automatic_pipeline_stage(target)


def latest_outreach_at(drafts: list[Mapping[str, Any]]) -> str | None:
    """Return the newest human-confirmed send timestamp for a firm."""
    timestamps = [str(draft.get("sent_at")) for draft in drafts if draft.get("sent_at")]
    return max(timestamps) if timestamps else None


def outreach_queue_state(
    target: Mapping[str, Any], drafts: list[Mapping[str, Any]]
) -> str:
    """Derive the acquisition queue from send records and CRM advancement.

    A draft, contact, or stage label never counts as a send. ``sent_at`` is set
    only by the human-confirmed mark-sent RPC and is therefore the authoritative
    source for initial outreach.
    """
    relationship = effective_relationship(target)
    stage = effective_pipeline_stage(target)
    if relationship != "Cold Prospect":
        return OUTREACH_NOT_APPLICABLE
    if stage in _OUTREACH_ADVANCEMENT_STAGES:
        return OUTREACH_ADVANCED
    if latest_outreach_at(drafts) is None:
        return OUTREACH_NOT_SENT
    return OUTREACH_AWAITING_RESPONSE


def pipeline_visible(target: Mapping[str, Any]) -> bool:
    """An active workflow, not relationship status alone, controls visibility."""
    active_value = target.get("pipeline_active", True)
    active = active_value if isinstance(active_value, bool) else (
        str(active_value).strip().lower() not in {"false", "0", "no"}
    )
    if not active:
        return False

    relationship = effective_relationship(target)
    stage_override = str(target.get("pipeline_stage_override") or "").strip()
    # A manual active-stage override is the explicit reopen mechanism for a
    # previously terminal relationship such as Not Interested.
    if relationship in TERMINAL_RELATIONSHIPS and not stage_override:
        return False
    return effective_pipeline_stage(target) in ACTIVE_PIPELINE_STAGES


def enrich_target(
    target: Mapping[str, Any], *, automatic_stage: str | None = None
) -> dict[str, Any]:
    """Return a browser-safe target row with effective values and provenance."""
    row = dict(target)
    relationship_auto = str(row.get("relationship_status_auto") or "").strip() or (
        canonical_relationship(row.get("relationship_status"), row.get("contact_status"))
    )
    stage_auto = automatic_stage or automatic_pipeline_stage(row)
    relationship = str(row.get("relationship_status_override") or "").strip() or relationship_auto
    stage = str(row.get("pipeline_stage_override") or "").strip() or stage_auto
    row.update({
        "relationship_status_auto": relationship_auto,
        "pipeline_stage_auto": stage_auto,
        "relationship_status_effective": relationship,
        "pipeline_stage_effective": stage,
        "relationship_status_source": (
            "manual" if row.get("relationship_status_override")
            else str(row.get("relationship_status_auto_source") or "automatic")
        ),
        "pipeline_stage_source": (
            "manual" if row.get("pipeline_stage_override")
            else str(row.get("pipeline_stage_auto_source") or "automatic")
        ),
        "relationship_status_is_overridden": bool(row.get("relationship_status_override")),
        "pipeline_stage_is_overridden": bool(row.get("pipeline_stage_override")),
        "effective_sponsorship_tier": effective_sponsorship_tier(row),
    })
    row["pipeline_visible"] = pipeline_visible(row)
    row["contact_status"] = legacy_contact_status(relationship)
    return row
