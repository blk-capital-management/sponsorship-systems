"""Dashboard orchestration around the existing validated pipeline.

This module owns storage translation only. Research, status derivation, Hunter
gating, contact ranking/verification, routing, drafting, and validation remain
in their original modules.
"""

from __future__ import annotations

import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from common.config import load_settings
from common.http import Fetcher
from common.logging import get_logger
from common.namespaces import build_contact_provenance
from common.owners import normalize_owner
from common.slugify import firm_slug
from contacts.discover import discover_target_contacts
from contacts.gate import evaluate_pre_hunter_gate
from contacts.hunter_guard import HunterGuard, normalize_domain
from contacts.providers.hunter import HunterProvider
from contacts.record import ContactRecord
from contacts.verify import verify_rows
from dashboard.auth import DashboardUser
from dashboard.models import IntakeFirm
from dashboard.storage import SupabaseConfigurationError, SupabaseStorage
from drafts.generate import generate_draft
from drafts.routing import COLD_PROSPECT
from research.fetch import build_artifact, crawl_firm, validate_artifact
from scripts.derive_target_status import CrmRow, derive, normalize_firm


log = get_logger("dashboard.services")


class DashboardServiceError(RuntimeError):
    """A safe, user-facing pipeline or state error."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _eq(value: str) -> str:
    return f"eq.{value}"


def _bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _owner_is(row: dict[str, Any], owner: str, field: str = "owner") -> bool:
    return str(row.get(field) or "").strip().lower() == owner


def dashboard_settings() -> dict[str, Any]:
    """Copy pipeline settings and put ephemeral crawl cache in writable /tmp.

    Vercel Functions cannot persist project-directory writes. Supabase stores
    the durable artifact, so only the existing HTML cache needs a runtime-safe
    location; no research or ranking behavior changes.
    """
    settings = deepcopy(load_settings())
    settings["research"]["cache_dir"] = os.getenv(
        "BLK_BRIDGE_CACHE_DIR", "/tmp/blk_bridge_research_cache"
    )
    return settings


def get_target(
    storage: SupabaseStorage, user: DashboardUser, target_id: str
) -> dict[str, Any]:
    rows = storage.select(
        "targets", user.access_token, lane=user.owner, params={"id": _eq(target_id), "limit": "1"}
    )
    if len(rows) != 1 or not _owner_is(rows[0], user.owner):
        raise DashboardServiceError(
            "Target was not found in your owner lane. RLS may have denied it."
        )
    return rows[0]


def get_artifact(
    storage: SupabaseStorage, user: DashboardUser, target_id: str
) -> dict[str, Any] | None:
    rows = storage.select(
        "research_artifacts",
        user.access_token, lane=user.owner,
        params={"target_id": _eq(target_id), "limit": "1"},
    )
    if not rows:
        return None
    if not _owner_is(rows[0], user.owner):
        raise DashboardServiceError(
            "Research was not found in your owner lane. RLS may have denied it."
        )
    return rows[0].get("artifact")


def _intake_error_reason(exc: Exception) -> str:
    """Turn a storage rejection into something an operator can act on."""
    text = str(exc)
    lowered = text.lower()
    if "targets_firm_slug_key" in lowered or "firm_slug" in lowered:
        return "A firm with this slug already exists."
    if "targets_domain_unique_idx" in lowered or "domain" in lowered:
        return "This domain is already on another target."
    if "targets_email_format_sourced" in lowered:
        return "An email format needs the source URL it was read off."
    return f"Storage rejected this row: {text[:200]}"


def _existing_target_index(
    storage: SupabaseStorage, user: DashboardUser
) -> tuple[set[str], set[str], dict[str, str]]:
    """Slugs, domains, and normalized firm names already in the owner's lane."""
    try:
        existing = storage.select(
            "targets", user.access_token, lane=user.owner,
            params={"select": "firm,firm_slug,domain", "limit": "1000"},
        )
    except Exception as exc:  # pragma: no cover - surfaced as a warning, not a failure
        log.warning("Could not read existing targets for dedupe: %s", exc)
        return set(), set(), {}

    slugs = {str(row.get("firm_slug") or "") for row in existing}
    domains = {str(row.get("domain") or "").lower() for row in existing if row.get("domain")}
    by_name = {
        normalize_firm(str(row.get("firm") or "")): str(row.get("firm") or "")
        for row in existing if row.get("firm")
    }
    return slugs, domains, by_name


def intake_targets(
    storage: SupabaseStorage, user: DashboardUser, firms: Iterable[IntakeFirm]
) -> dict[str, Any]:
    """Add sourced leads, one row at a time.

    Per-row inserts on purpose. firm_slug is unique and there is a unique index on
    lower(domain), so a single bulk insert loses the whole batch when one firm is
    already known. Sourcing arrives in messy batches; losing 29 good leads to 1
    duplicate is the wrong trade.

    Returns {"accepted": [...], "skipped": [...], "warnings": [...]}.
    """
    known_slugs, known_domains, known_names = _existing_target_index(storage, user)

    accepted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    seen_slugs: set[str] = set()
    seen_domains: set[str] = set()

    for item in firms:
        slug = firm_slug(item.firm)
        if not slug:
            skipped.append({"firm": item.firm, "reason": "Firm name has no usable slug."})
            continue
        if slug in seen_slugs:
            skipped.append({"firm": item.firm, "reason": "Duplicate firm in this batch."})
            continue
        if slug in known_slugs:
            skipped.append({"firm": item.firm, "reason": "Already in your pipeline."})
            continue

        domain = (item.domain or "").lower()
        if domain and (domain in seen_domains or domain in known_domains):
            skipped.append({
                "firm": item.firm,
                "reason": f"Domain {domain} is already on another firm.",
            })
            continue

        # Near-duplicate is a warning, not a block. normalize_firm is deliberately
        # conservative, and the operator is better placed to judge than we are.
        near = known_names.get(normalize_firm(item.firm))
        if near and near != item.firm:
            warnings.append({
                "firm": item.firm,
                "warning": f"Looks similar to existing target {near!r}. Added anyway.",
            })
        if not item.firm_type_recognized:
            warnings.append({
                "firm": item.firm,
                "warning": (
                    f"Category {item.firm_type!r} is outside the known list. "
                    "Stored as given; contact title routing may not apply."
                ),
            })

        row = {
            "owner": user.owner,
            "firm": item.firm,
            "firm_slug": slug,
            "domain": item.domain,
            "website": item.website,
            "linkedin_url": item.linkedin_url,
            "region": item.region or "US",
            "firm_type": item.firm_type,
            "tier_target": item.tier_target,
            "crm_status": "New Lead",
            "priority": item.priority,
            "relationship": "prospect",
            "notes": item.notes,
            "contact_status": "cold_prospect",
            "has_known_contact": False,
            "contact_needs_refresh": False,
            "email_format": item.email_format,
            "email_format_source_url": item.email_format_source_url,
            "created_by": user.user_id,
        }
        try:
            inserted_rows = storage.insert("targets", row, user.access_token, lane=user.owner)
        except Exception as exc:
            log.warning("Intake failed for %s: %s", item.firm, exc)
            skipped.append({"firm": item.firm, "reason": _intake_error_reason(exc)})
            continue

        if not inserted_rows:
            skipped.append({"firm": item.firm, "reason": "Insert returned no row."})
            continue

        seen_slugs.add(slug)
        if domain:
            seen_domains.add(domain)
        accepted.append(inserted_rows[0])

    if not accepted and skipped:
        log.info("Intake added nothing: %d row(s) skipped.", len(skipped))

    inserted = accepted
    missing_domain = [row for row in inserted if not str(row.get("domain") or "").strip()]
    for row in missing_domain:
        storage.insert(
            "manual_queue",
            {
                "target_id": row["id"],
                "owner": user.owner,
                "firm": row["firm"],
                "firm_slug": row["firm_slug"],
                "domain": "",
                "confidence": "low",
                "reason": "A verified firm domain is required before research can run.",
                "gaps": ["domain missing from human batch intake"],
                "source_stage": "intake",
            },
            user.access_token, lane=user.owner,
        )
    storage.insert(
        "action_runs",
        {
            "owner": user.owner,
            "actor_id": user.user_id,
            "action_type": "batch_intake",
            "status": "completed",
            "target_ids": [row["id"] for row in inserted],
            "details": {
                "count": len(inserted),
                "missing_domain": len(missing_domain),
                "skipped": len(skipped),
            },
            "completed_at": utc_now().isoformat(),
        },
        user.access_token, lane=user.owner,
        return_rows=False,
    )
    return {"accepted": accepted, "skipped": skipped, "warnings": warnings}


def _upsert_manual_queue(
    storage: SupabaseStorage,
    user: DashboardUser,
    target: dict[str, Any],
    *,
    source_stage: str,
    reason: str,
    confidence: str,
    gaps: list[str],
    queued_at: str | None = None,
) -> None:
    """Record one open manual-queue item per (target, stage).

    The table keeps a unique index on that pair while an item is open, so a
    repeat failure has to update the existing row rather than pile up a second
    one. A queue that only grows stops being read.
    """
    existing = storage.select(
        "manual_queue",
        user.access_token, lane=user.owner,
        params={
            "target_id": _eq(str(target["id"])),
            "source_stage": _eq(source_stage),
            "resolved_at": "is.null",
            "limit": "1",
        },
    )
    values = {
        "owner": target["owner"],
        "firm": target["firm"],
        "firm_slug": target["firm_slug"],
        "domain": str(target.get("domain") or ""),
        "confidence": confidence,
        "reason": reason,
        "gaps": gaps,
        "source_stage": source_stage,
        "queued_at": queued_at or utc_now().isoformat(),
    }
    if existing:
        storage.update(
            "manual_queue", values, user.access_token, lane=user.owner,
            params={"id": _eq(str(existing[0]["id"]))}, return_rows=False,
        )
    else:
        storage.insert(
            "manual_queue", {"target_id": target["id"], **values},
            user.access_token, lane=user.owner, return_rows=False,
        )


def _write_manual_queue(
    storage: SupabaseStorage,
    user: DashboardUser,
    target: dict[str, Any],
    artifact: dict[str, Any],
) -> None:
    """Route a firm whose research produced nothing citable."""
    _upsert_manual_queue(
        storage, user, target,
        source_stage="research",
        reason=(
            "no alignment hooks with a firm_claim_source"
            if not artifact.get("alignment_hooks")
            else "research confidence is low"
        ),
        confidence=artifact["confidence"],
        gaps=artifact.get("gaps", []),
        queued_at=artifact.get("fetched_at"),
    )


def _write_pipeline_failure(
    storage: SupabaseStorage,
    user: DashboardUser,
    target: dict[str, Any],
    *,
    source_stage: str,
    reason: str,
    detail: str,
) -> None:
    """Keep per-target batch failures visible without inventing pipeline data."""
    _upsert_manual_queue(
        storage, user, target,
        source_stage=source_stage,
        reason=reason,
        confidence="low",
        gaps=[detail[:1000]],
    )


def _queue_owned_batch_failure(
    storage: SupabaseStorage,
    user: DashboardUser,
    target_id: str,
    *,
    source_stage: str,
    reason: str,
    detail: str,
) -> None:
    """Queue an error only when the target is still readable in the actor's lane."""
    try:
        target = get_target(storage, user, target_id)
        _write_pipeline_failure(
            storage,
            user,
            target,
            source_stage=source_stage,
            reason=reason,
            detail=detail,
        )
    except Exception:
        # An invalid or cross-owner id must not create a row or reveal target data.
        return


def research_target(
    storage: SupabaseStorage,
    user: DashboardUser,
    target_id: str,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    target = get_target(storage, user, target_id)
    if not str(target.get("domain") or "").strip():
        raise DashboardServiceError("Research requires a human-supplied firm domain.")
    settings = dashboard_settings()
    fetcher = Fetcher(settings["research"])
    crawled, texts, blocking = crawl_firm(
        fetcher, target, settings["research"], refresh
    )
    artifact = build_artifact(
        target, crawled, texts, settings["research"], blocking
    )
    validate_artifact(artifact)
    storage.upsert(
        "research_artifacts",
        {
            "target_id": target["id"],
            "owner": target["owner"],
            "confidence": artifact["confidence"],
            "hook_count": len(artifact["alignment_hooks"]),
            "artifact": artifact,
            "gaps": artifact.get("gaps", []),
            "researched_at": artifact["fetched_at"],
        },
        user.access_token, lane=user.owner,
        on_conflict="target_id",
        return_rows=False,
    )
    if artifact["confidence"] == "low" or not artifact["alignment_hooks"]:
        _write_manual_queue(storage, user, target, artifact)
    else:
        storage.update(
            "manual_queue", {"resolved_at": utc_now().isoformat()}, user.access_token, lane=user.owner,
            params={
                "target_id": _eq(str(target["id"])),
                "source_stage": "eq.research",
                "resolved_at": "is.null",
            }, return_rows=False,
        )
    return artifact


def _run_owned_batch(
    storage: SupabaseStorage,
    user: DashboardUser,
    target_ids: list[str],
    *,
    action_type: str,
    failure_reason: str,
    run_one,
) -> list[dict[str, Any]]:
    """Run one action across a selection, opening and closing an action_run.

    A per-target failure is recorded and routed to the manual queue rather than
    aborting the rest, so a batch of twenty does not lose nineteen good results
    to one bad target. The run closes as failed when anything failed, but the
    successful work is still returned.
    """
    run = storage.insert(
        "action_runs",
        {
            "owner": user.owner,
            "actor_id": user.user_id,
            "action_type": action_type,
            "status": "running",
            "target_ids": target_ids,
        },
        user.access_token, lane=user.owner,
    )[0]

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for target_id in target_ids:
        try:
            results.append(run_one(target_id))
        except Exception as exc:
            detail = str(exc)
            errors.append({"target_id": target_id, "error": detail})
            _queue_owned_batch_failure(
                storage, user, target_id,
                source_stage=action_type,
                reason=failure_reason,
                detail=detail,
            )

    storage.update(
        "action_runs",
        {
            "status": "failed" if errors else "completed",
            "details": {"results": results, "errors": errors},
            "error": f"{len(errors)} target(s) failed" if errors else "",
            "completed_at": utc_now().isoformat(),
        },
        user.access_token, lane=user.owner,
        params={"id": _eq(str(run["id"]))},
        return_rows=False,
    )
    return results + [
        {"target_id": error["target_id"], "error": error["error"]} for error in errors
    ]


def research_batch(
    storage: SupabaseStorage, user: DashboardUser, target_ids: list[str]
) -> list[dict[str, Any]]:
    def run_one(target_id: str) -> dict[str, Any]:
        artifact = research_target(storage, user, target_id)
        return {
            "target_id": target_id,
            "firm": artifact["firm"],
            "confidence": artifact["confidence"],
            "hooks": len(artifact["alignment_hooks"]),
        }

    return _run_owned_batch(
        storage, user, target_ids,
        action_type="research",
        failure_reason="Research failed and requires manual review.",
        run_one=run_one,
    )


def _crm_rows_from_database(raw_rows: list[dict[str, Any]]) -> list[CrmRow]:
    """Reconstruct CrmRow objects from crm_records, the inverse of the mapping
    scripts/seed_supabase.py::seed_crm writes when it imports the CRM workbook.
    """
    return [
        CrmRow(
            tab=str(row.get("tab") or ""),
            row_number=int(row.get("row_number") or 0),
            firm=str(row.get("firm") or ""),
            status=str(row.get("status") or ""),
            expiration_raw=row.get("expiration_raw"),
            contacts=list(row.get("contacts") or []),
            is_ledger=bool(row.get("is_ledger")),
            record_id=str(row.get("record_id") or ""),
            tier=str(row.get("tier") or ""),
            emails=list(row.get("emails") or []),
            decline_reason=str(row.get("decline_reason") or ""),
        )
        for row in raw_rows
    ]


def derive_status(
    storage: SupabaseStorage, user: DashboardUser, target_id: str
) -> dict[str, Any]:
    target = get_target(storage, user, target_id)
    try:
        raw_rows = storage.service_select(
            "crm_records",
            params={"firm_normalized": _eq(normalize_firm(target["firm"]))},
        )
    except SupabaseConfigurationError as exc:
        raise DashboardServiceError(
            "Status derivation requires the server-only Supabase secret key."
        ) from exc
    result = derive(target["firm"], _crm_rows_from_database(raw_rows), date.today())
    deciding = result.deciding
    values = {
        "contact_status": result.contact_status,
        "has_known_contact": result.has_known_contact,
        "contact_needs_refresh": bool(target.get("contact_needs_refresh")),
        "relationship_record_id": deciding.record_id if deciding else "",
        "relationship_tier": deciding.tier if deciding else "",
        "relationship_status": deciding.status if deciding else "",
        "relationship_contact_name": "; ".join(deciding.contacts) if deciding else "",
        "relationship_contact_email": "; ".join(deciding.emails) if deciding else "",
        "relationship_expiration": (
            deciding.expiration.isoformat() if deciding and deciding.expiration else None
        ),
        "relationship_decline_reason": deciding.decline_reason if deciding else "",
        "relationship_crm_source": deciding.where() if deciding else "",
    }
    updated = storage.update(
        "targets", values, user.access_token, lane=user.owner,
        params={"id": _eq(target_id)},
    )
    return updated[0] if updated else {**target, **values}


def derive_status_batch(
    storage: SupabaseStorage, user: DashboardUser, target_ids: list[str]
) -> list[dict[str, Any]]:
    return _run_owned_batch(
        storage, user, target_ids,
        action_type="derive_status",
        failure_reason="Contact status derivation failed and requires manual review.",
        run_one=lambda target_id: derive_status(storage, user, target_id),
    )


class SupabaseHunterGuard(HunterGuard):
    """Hunter guard whose audit sink is the durable Supabase table."""

    def __init__(
        self,
        storage: SupabaseStorage,
        user: DashboardUser,
        *,
        max_calls: int,
        target_domains: set[str],
        action_run_id: str,
    ):
        super().__init__(max_calls=max_calls, target_domains=target_domains)
        self.storage = storage
        self.user = user
        self.run_id = action_run_id

    def _log(self, endpoint: str, domain: str, credits: int, note: str) -> None:
        self.storage.service_insert(
            "hunter_usage",
            {
                "owner": self.user.owner,
                "actor_id": self.user.user_id,
                "timestamp": utc_now().isoformat(),
                "endpoint": endpoint,
                "domain": domain,
                "credits": credits,
                "run_id": self.run_id,
                "note": note,
            },
            return_rows=False,
        )

    def historical_credits(self) -> int:
        rows = self.storage.service_select("hunter_usage", select="credits")
        return sum(int(row.get("credits") or 0) for row in rows)


def _hunter_provider(
    storage: SupabaseStorage,
    user: DashboardUser,
    *,
    max_calls: int,
    target_domains: set[str],
    action_run_id: str,
) -> HunterProvider:
    key = os.getenv("HUNTER_API_KEY", "").strip()
    if not key:
        raise DashboardServiceError("HUNTER_API_KEY is not configured on the server.")
    guard = SupabaseHunterGuard(
        storage, user, max_calls=max_calls, target_domains=target_domains,
        action_run_id=action_run_id,
    )
    return HunterProvider(api_key=key, guard=guard)


def hunter_balance(
    storage: SupabaseStorage, user: DashboardUser
) -> dict[str, Any]:
    """Read the provider balance, reporting why rather than returning a bare null.

    HunterProvider.account() swallows its own errors, and the audit log this
    call writes first depends on the Supabase service key. A null balance can
    therefore mean a bad Hunter key, a bad service key, or a provider outage.
    Collapsing all three into "Unavailable" hides a broken deployment behind
    what looks like a provider hiccup, so the cause travels with the result.
    """
    targets = storage.select("targets", user.access_token, lane=user.owner, select="domain")
    provider = _hunter_provider(
        storage, user, max_calls=1,
        target_domains={normalize_domain(row.get("domain", "")) for row in targets},
        action_run_id=f"balance-{utc_now():%Y%m%dT%H%M%SZ}",
    )
    account = provider.account() or {}
    if not account:
        log.warning("Hunter account balance came back empty for owner %s.", user.owner)
        return {
            "used": None, "available": None, "remaining": None,
            "error": "Hunter returned no account balance. Check HUNTER_API_KEY and "
                     "the Supabase service key, which the credit audit log writes with.",
        }
    searches = (account.get("requests") or {}).get("searches") or {}
    used = searches.get("used")
    available = searches.get("available")
    return {
        "used": int(used) if used is not None else None,
        "available": int(available) if available is not None else None,
        "remaining": (
            max(0, int(available) - int(used))
            if used is not None and available is not None else None
        ),
        "error": None,
    }


def preview_contact_run(
    storage: SupabaseStorage, user: DashboardUser, target_ids: list[str]
) -> dict[str, Any]:
    eligible: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for target_id in target_ids:
        target = get_target(storage, user, target_id)
        decision = evaluate_pre_hunter_gate(target)
        artifact = get_artifact(storage, user, target_id)
        if decision.skip:
            skipped.append({"target_id": target_id, "reason": decision.describe()})
        elif not artifact or artifact.get("confidence") == "low" or not artifact.get("alignment_hooks"):
            skipped.append({"target_id": target_id, "reason": "research is missing or manual-queue"})
        else:
            eligible.append(target)

    max_per_run = int(
        dashboard_settings()["contacts"]["hunter"]["max_calls_per_run"]
    )
    balance = hunter_balance(storage, user)
    estimated_max = len(eligible) * 11
    remaining = balance.get("remaining")
    credits_max = min(
        max_per_run,
        estimated_max,
        int(remaining) if remaining is not None else max_per_run,
    )
    expires = utc_now() + timedelta(minutes=15)
    run = storage.insert(
        "action_runs",
        {
            "owner": user.owner,
            "actor_id": user.user_id,
            "action_type": "contact_preview",
            "status": "waiting_confirmation",
            "target_ids": [row["id"] for row in eligible],
            "credits_min": 0,
            "credits_max": credits_max,
            "hunter_balance_before": balance["remaining"],
            "details": {"skipped": skipped, "requested_target_ids": target_ids},
            "expires_at": expires.isoformat(),
        },
        user.access_token, lane=user.owner,
    )[0]
    return {
        "run_id": run["id"],
        "eligible": [{"id": row["id"], "firm": row["firm"]} for row in eligible],
        "skipped": skipped,
        "credits_min": 0,
        "credits_max": credits_max,
        "hunter_balance": balance,
        "expires_at": expires.isoformat(),
    }


def _contact_db_row(
    target: dict[str, Any], row: dict[str, Any], *, dropped: bool
) -> dict[str, Any]:
    discovery_url = str(row.get("source_url") or row.get(
        "contact_provenance_discovery_url") or "")
    pattern_url = str(row.get("pattern_source_url") or row.get(
        "contact_provenance_pattern_url") or "")
    provenance = build_contact_provenance(
        discovery_url,
        pattern_url,
        method="pattern_inference" if row.get("pattern") else "provider_published",
        provider=str(row.get("verification_provider") or "hunter"),
    )
    rank = row.get("title_rank")
    score = row.get("verification_score")
    confidence = row.get("pattern_confidence")
    return {
        "target_id": target["id"],
        "owner": target["owner"],
        "name": str(row.get("name") or ""),
        "title": str(row.get("title") or ""),
        "title_rank": int(rank) if str(rank or "").isdigit() else None,
        "email": str(row.get("email") or ""),
        "pattern": str(row.get("pattern") or ""),
        "pattern_confidence": float(confidence) if str(confidence or "").strip() else None,
        "verification_score": int(score) if str(score or "").isdigit() else None,
        "verification_status": str(row.get("verification_status") or ""),
        "verification_provider": str(row.get("verification_provider") or ""),
        "contact_provenance": provenance,
        "dropped": dropped,
        "drop_reason": str(row.get("drop_reason") or ""),
    }


def run_contact_discovery(
    storage: SupabaseStorage,
    user: DashboardUser,
    run_id: str,
    confirmed_credit_cap: int,
) -> dict[str, Any]:
    previews = storage.select(
        "action_runs", user.access_token, lane=user.owner,
        params={"id": _eq(run_id), "status": "eq.waiting_confirmation", "limit": "1"},
    )
    if len(previews) != 1:
        raise DashboardServiceError("Contact preview is missing or already consumed.")
    preview = previews[0]
    expires = datetime.fromisoformat(str(preview["expires_at"]).replace("Z", "+00:00"))
    if expires < utc_now():
        raise DashboardServiceError("Contact preview expired. Refresh the live estimate.")
    if confirmed_credit_cap > int(preview.get("credits_max") or 0):
        raise DashboardServiceError("Confirmed cap exceeds the previewed hard maximum.")
    target_ids = [str(value) for value in (preview.get("target_ids") or [])]
    if target_ids and confirmed_credit_cap < 1:
        raise DashboardServiceError("At least one call must be authorized for an eligible run.")
    targets = [get_target(storage, user, target_id) for target_id in target_ids]
    domains = {normalize_domain(row["domain"]) for row in targets}
    provider = _hunter_provider(
        storage, user, max_calls=confirmed_credit_cap, target_domains=domains,
        action_run_id=run_id,
    )
    storage.update(
        "action_runs",
        {"action_type": "contact_discovery", "status": "running"},
        user.access_token, lane=user.owner, params={"id": _eq(run_id)}, return_rows=False,
    )

    results: list[dict[str, Any]] = []
    try:
        for target in targets:
            decision = evaluate_pre_hunter_gate(target)
            if decision.skip:
                provider.guard.record_skip(
                    "domain-search", target["domain"],
                    note=f"SKIPPED: {decision.describe()}",
                )
                results.append({"firm": target["firm"], "status": "skipped", "kept": 0})
                continue
            artifact = get_artifact(storage, user, str(target["id"]))
            if not artifact:
                results.append({"firm": target["firm"], "status": "no_research", "kept": 0})
                continue
            discovered = discover_target_contacts(
                target,
                artifact,
                provider=provider,
                persist_pattern=False,
                settings=dashboard_settings(),
            )
            kept, dropped = verify_rows(
                target, discovered, provider=provider, settings=dashboard_settings()
            )
            storage.delete(
                "contacts", user.access_token, lane=user.owner,
                params={"target_id": _eq(str(target["id"]))},
            )
            rows = [
                *[_contact_db_row(target, row, dropped=False) for row in kept],
                *[_contact_db_row(target, row, dropped=True) for row in dropped],
            ]
            if rows:
                storage.insert("contacts", rows, user.access_token, lane=user.owner, return_rows=False)
            results.append({
                "firm": target["firm"], "status": "completed",
                "discovered": len(discovered), "kept": len(kept), "dropped": len(dropped),
            })
    except Exception as exc:
        storage.update(
            "action_runs",
            {"status": "failed", "credits_spent": provider.guard.credits_spent,
             "error": str(exc), "details": {"results": results},
             "completed_at": utc_now().isoformat()},
            user.access_token, lane=user.owner, params={"id": _eq(run_id)}, return_rows=False,
        )
        raise

    storage.update(
        "action_runs",
        {"status": "completed", "credits_spent": provider.guard.credits_spent,
         "details": {"results": results}, "completed_at": utc_now().isoformat()},
        user.access_token, lane=user.owner, params={"id": _eq(run_id)}, return_rows=False,
    )
    return {"run_id": run_id, "credits_spent": provider.guard.credits_spent,
            "results": results}


def _contact_record(row: dict[str, Any]) -> ContactRecord:
    return ContactRecord(
        name=str(row.get("name") or ""),
        owner=normalize_owner(row.get("owner")),
        title=str(row.get("title") or ""),
        email=str(row.get("email") or ""),
        title_rank=str(row.get("title_rank") or ""),
        pattern=str(row.get("pattern") or ""),
        pattern_confidence=str(row.get("pattern_confidence") or ""),
        verification_score=str(row.get("verification_score") or ""),
        verification_status=str(row.get("verification_status") or ""),
        verification_provider=str(row.get("verification_provider") or ""),
        contact_provenance=row.get("contact_provenance") or build_contact_provenance(),
    )


def _generate_record(
    target: dict[str, Any],
    artifact: dict[str, Any] | None,
    contact: ContactRecord | None,
    paragraph: str | None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="blk-bridge-draft-") as tmp:
        return generate_draft(
            artifact,
            contact,
            target=target,
            firm_specific_paragraph=paragraph,
            out_dir=Path(tmp),
            review_dir=Path(tmp),
        )


def generate_owner_draft(
    storage: SupabaseStorage,
    user: DashboardUser,
    *,
    target_id: str,
    contact_id: str | None,
    paragraph: str | None,
) -> dict[str, Any]:
    target = get_target(storage, user, target_id)
    artifact = get_artifact(storage, user, target_id)
    contact: ContactRecord | None = None
    if target["contact_status"] == COLD_PROSPECT:
        params = {
            "target_id": _eq(target_id), "dropped": "eq.false",
            "order": "verification_score.desc.nullslast", "limit": "1",
        }
        if contact_id:
            params["id"] = _eq(contact_id)
        rows = storage.select("contacts", user.access_token, lane=user.owner, params=params)
        if not rows:
            raise DashboardServiceError(
                "A verified owner-scoped contact is required for a cold draft."
            )
        contact = _contact_record(rows[0])
    record = _generate_record(target, artifact, contact, paragraph)
    draft_id = storage.rpc(
        "save_validated_draft",
        {"p_target_id": target_id, "p_payload": record},
        user.access_token, lane=user.owner,
    )
    return {"id": draft_id, **record, "status": "pending_review"}


def review_draft(
    storage: SupabaseStorage,
    user: DashboardUser,
    draft_id: str,
    action: str,
    reason: str | None,
) -> dict[str, Any]:
    result = storage.rpc(
        "review_draft",
        {"p_draft_id": draft_id, "p_action": action, "p_reason": reason},
        user.access_token, lane=user.owner,
    )
    return dict(result or {})


def mark_draft_sent(
    storage: SupabaseStorage,
    user: DashboardUser,
    draft_id: str,
) -> dict[str, Any]:
    """Record that a human copied an approved draft into Gmail and sent it.

    Bridge never transmits. This is the human reporting back, so that a later
    run can tell which firms were already contacted.
    """
    result = storage.rpc(
        "mark_draft_sent", {"p_draft_id": draft_id}, user.access_token, lane=user.owner
    )
    return dict(result or {})


def resolve_manual_item(
    storage: SupabaseStorage,
    user: DashboardUser,
    item_id: str,
    note: str,
) -> dict[str, Any]:
    """Close a manual-queue item the owner handled outside Bridge."""
    result = storage.rpc(
        "resolve_manual_queue_item",
        {"p_item_id": item_id, "p_note": note},
        user.access_token, lane=user.owner,
    )
    return dict(result or {})


def dashboard_state(
    storage: SupabaseStorage, user: DashboardUser
) -> dict[str, Any]:
    targets = storage.select(
        "targets", user.access_token, lane=user.owner, params={"order": "priority.asc,firm.asc"}
    )
    research = storage.select("research_artifacts", user.access_token, lane=user.owner)
    contacts = storage.select(
        "contacts", user.access_token, lane=user.owner,
        params={"dropped": "eq.false", "order": "verification_score.desc.nullslast"},
    )
    drafts = storage.select(
        "drafts", user.access_token, lane=user.owner, params={"order": "generated_at.desc"}
    )
    manual = storage.select(
        "manual_queue", user.access_token, lane=user.owner,
        params={"resolved_at": "is.null", "order": "queued_at.desc"},
    )
    scoped_groups = {
        "targets": targets,
        "research": research,
        "contacts": contacts,
        "drafts": drafts,
        "manual queue": manual,
    }
    for label, rows in scoped_groups.items():
        if any(not _owner_is(row, user.owner) for row in rows):
            raise DashboardServiceError(
                f"Owner-scope violation blocked while loading {label}."
            )
    targets_with_gate: list[dict[str, Any]] = []
    for target in targets:
        decision = evaluate_pre_hunter_gate(target)
        targets_with_gate.append({
            **target,
            "hunter_gate": {
                "skip": decision.skip,
                "status": "skipped" if decision.skip else "eligible",
                "reason": decision.describe(),
            },
        })
    # A provider problem must not take the whole workspace down, but it must not
    # be invisible either: the reason is carried through to the UI.
    try:
        balance = hunter_balance(storage, user)
    except Exception as exc:
        log.warning("Hunter balance lookup failed for owner %s: %s", user.owner, exc)
        balance = {
            "used": None, "available": None, "remaining": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "user": {
            "id": user.user_id,
            "email": user.email,
            "owner": user.owner,
            "display_name": user.display_name,
            "gmail_sender": user.gmail_sender,
            "role": user.role,
            "actor_display_name": user.actor_display_name,
            "available_lanes": list(user.available_lanes),
        },
        "targets": targets_with_gate,
        "research": research,
        "contacts": contacts,
        "drafts": drafts,
        "manual_queue": manual,
        "hunter_balance": balance,
        "counts": {
            "targets": len(targets),
            "cold_prospect": sum(t.get("contact_status") == "cold_prospect" for t in targets),
            "existing_partner": sum(t.get("contact_status") == "existing_partner" for t in targets),
            "lapsed_partner": sum(t.get("contact_status") == "lapsed_partner" for t in targets),
            "pending_review": sum(d.get("status") == "pending_review" for d in drafts),
            "approved": sum(d.get("status") == "approved" for d in drafts),
            "sent": sum(d.get("status") == "sent" for d in drafts),
            # Exceeding the per-mailbox daily cap damages domain reputation,
            # which degrades deliverability on live sponsor threads. The cap was
            # unenforceable until drafts recorded when they were sent.
            "sent_today": sum(
                str(d.get("sent_at") or "").startswith(utc_now().date().isoformat())
                for d in drafts
            ),
            "daily_send_cap": int(
                (dashboard_settings().get("send") or {}).get("daily_cap_per_mailbox", 40)
            ),
            "manual_queue": len(manual),
        },
    }
