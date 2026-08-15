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
        "targets", user.access_token, params={"id": _eq(target_id), "limit": "1"}
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
        user.access_token,
        params={"target_id": _eq(target_id), "limit": "1"},
    )
    if not rows:
        return None
    if not _owner_is(rows[0], user.owner):
        raise DashboardServiceError(
            "Research was not found in your owner lane. RLS may have denied it."
        )
    return rows[0].get("artifact")


def intake_targets(
    storage: SupabaseStorage, user: DashboardUser, firms: Iterable[IntakeFirm]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in firms:
        slug = firm_slug(item.firm)
        if not slug or slug in seen:
            raise DashboardServiceError(
                f"Duplicate or unusable firm name in batch: {item.firm!r}."
            )
        seen.add(slug)
        rows.append({
            "owner": user.owner,
            "firm": item.firm,
            "firm_slug": slug,
            "domain": item.domain,
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
            "created_by": user.user_id,
        })

    try:
        inserted = storage.insert("targets", rows, user.access_token)
    except Exception as exc:
        raise DashboardServiceError(
            "Batch intake failed. Check for an existing firm slug or domain. " + str(exc)
        ) from exc

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
            user.access_token,
        )
    storage.insert(
        "action_runs",
        {
            "owner": user.owner,
            "actor_id": user.user_id,
            "action_type": "batch_intake",
            "status": "completed",
            "target_ids": [row["id"] for row in inserted],
            "details": {"count": len(inserted), "missing_domain": len(missing_domain)},
            "completed_at": utc_now().isoformat(),
        },
        user.access_token,
        return_rows=False,
    )
    return inserted


def _write_manual_queue(
    storage: SupabaseStorage,
    user: DashboardUser,
    target: dict[str, Any],
    artifact: dict[str, Any],
) -> None:
    existing = storage.select(
        "manual_queue",
        user.access_token,
        params={
            "target_id": _eq(str(target["id"])),
            "source_stage": "eq.research",
            "resolved_at": "is.null",
            "limit": "1",
        },
    )
    reason = (
        "no alignment hooks with a firm_claim_source"
        if not artifact.get("alignment_hooks")
        else "research confidence is low"
    )
    values = {
        "owner": target["owner"],
        "firm": target["firm"],
        "firm_slug": target["firm_slug"],
        "domain": target["domain"],
        "confidence": artifact["confidence"],
        "reason": reason,
        "gaps": artifact.get("gaps", []),
        "source_stage": "research",
        "queued_at": artifact.get("fetched_at") or utc_now().isoformat(),
    }
    if existing:
        storage.update(
            "manual_queue", values, user.access_token,
            params={"id": _eq(str(existing[0]["id"]))}, return_rows=False,
        )
    else:
        storage.insert(
            "manual_queue", {"target_id": target["id"], **values},
            user.access_token, return_rows=False,
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
    existing = storage.select(
        "manual_queue",
        user.access_token,
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
        "confidence": "low",
        "reason": reason,
        "gaps": [detail[:1000]],
        "source_stage": source_stage,
        "queued_at": utc_now().isoformat(),
    }
    if existing:
        storage.update(
            "manual_queue",
            values,
            user.access_token,
            params={"id": _eq(str(existing[0]["id"]))},
            return_rows=False,
        )
    else:
        storage.insert(
            "manual_queue",
            {"target_id": target["id"], **values},
            user.access_token,
            return_rows=False,
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
        user.access_token,
        on_conflict="target_id",
        return_rows=False,
    )
    if artifact["confidence"] == "low" or not artifact["alignment_hooks"]:
        _write_manual_queue(storage, user, target, artifact)
    else:
        storage.update(
            "manual_queue", {"resolved_at": utc_now().isoformat()}, user.access_token,
            params={
                "target_id": _eq(str(target["id"])),
                "source_stage": "eq.research",
                "resolved_at": "is.null",
            }, return_rows=False,
        )
    return artifact


def research_batch(
    storage: SupabaseStorage, user: DashboardUser, target_ids: list[str]
) -> list[dict[str, Any]]:
    run = storage.insert(
        "action_runs",
        {
            "owner": user.owner,
            "actor_id": user.user_id,
            "action_type": "research",
            "status": "running",
            "target_ids": target_ids,
        },
        user.access_token,
    )[0]
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for target_id in target_ids:
        try:
            artifact = research_target(storage, user, target_id)
            results.append({
                "target_id": target_id,
                "firm": artifact["firm"],
                "confidence": artifact["confidence"],
                "hooks": len(artifact["alignment_hooks"]),
            })
        except Exception as exc:
            detail = str(exc)
            errors.append({"target_id": target_id, "error": detail})
            _queue_owned_batch_failure(
                storage,
                user,
                target_id,
                source_stage="research",
                reason="Research failed and requires manual review.",
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
        user.access_token,
        params={"id": _eq(str(run["id"]))},
        return_rows=False,
    )
    return results + [{"target_id": e["target_id"], "error": e["error"]} for e in errors]


def _crm_rows_from_database(rows: list[dict[str, Any]]) -> list[CrmRow]:
    return [
        CrmRow(
            tab=str(row["tab"]),
            row_number=int(row["row_number"]),
            firm=str(row["firm"]),
            status=str(row.get("status") or ""),
            expiration_raw=row.get("expiration") or row.get("expiration_raw"),
            contacts=[str(v) for v in (row.get("contacts") or [])],
            is_ledger=bool(row.get("is_ledger")),
            record_id=str(row.get("record_id") or ""),
            tier=str(row.get("tier") or ""),
            emails=[str(v) for v in (row.get("emails") or [])],
            decline_reason=str(row.get("decline_reason") or ""),
        )
        for row in rows
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
        "targets", values, user.access_token,
        params={"id": _eq(target_id)},
    )
    return updated[0] if updated else {**target, **values}


def derive_status_batch(
    storage: SupabaseStorage, user: DashboardUser, target_ids: list[str]
) -> list[dict[str, Any]]:
    run = storage.insert(
        "action_runs",
        {
            "owner": user.owner,
            "actor_id": user.user_id,
            "action_type": "derive_status",
            "status": "running",
            "target_ids": target_ids,
        }, user.access_token,
    )[0]
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for target_id in target_ids:
        try:
            results.append(derive_status(storage, user, target_id))
        except Exception as exc:
            detail = str(exc)
            errors.append({"target_id": target_id, "error": detail})
            _queue_owned_batch_failure(
                storage,
                user,
                target_id,
                source_stage="derive_status",
                reason="Contact status derivation failed and requires manual review.",
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
        user.access_token, params={"id": _eq(str(run["id"]))}, return_rows=False,
    )
    return results + [
        {"target_id": error["target_id"], "error": error["error"]}
        for error in errors
    ]


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
    targets = storage.select("targets", user.access_token, select="domain")
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
        user.access_token,
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
        "action_runs", user.access_token,
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
        user.access_token, params={"id": _eq(run_id)}, return_rows=False,
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
                "contacts", user.access_token,
                params={"target_id": _eq(str(target["id"]))},
            )
            rows = [
                *[_contact_db_row(target, row, dropped=False) for row in kept],
                *[_contact_db_row(target, row, dropped=True) for row in dropped],
            ]
            if rows:
                storage.insert("contacts", rows, user.access_token, return_rows=False)
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
            user.access_token, params={"id": _eq(run_id)}, return_rows=False,
        )
        raise

    storage.update(
        "action_runs",
        {"status": "completed", "credits_spent": provider.guard.credits_spent,
         "details": {"results": results}, "completed_at": utc_now().isoformat()},
        user.access_token, params={"id": _eq(run_id)}, return_rows=False,
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
        rows = storage.select("contacts", user.access_token, params=params)
        if not rows:
            raise DashboardServiceError(
                "A verified owner-scoped contact is required for a cold draft."
            )
        contact = _contact_record(rows[0])
    record = _generate_record(target, artifact, contact, paragraph)
    draft_id = storage.rpc(
        "save_validated_draft",
        {"p_target_id": target_id, "p_payload": record},
        user.access_token,
    )
    return {"id": draft_id, **record, "status": "pending_review"}


def generate_cross_owner_draft(
    storage: SupabaseStorage,
    user: DashboardUser,
    *,
    target_owner: str,
    target_slug: str,
    paragraph: str | None,
    confirmation_text: str,
) -> dict[str, Any]:
    target_owner = normalize_owner(target_owner)
    if target_owner == user.owner:
        raise DashboardServiceError("Use the normal draft action for your own lane.")
    expected = (
        f"I confirm that {user.owner} is generating a draft for {target_owner}'s "
        f"target {target_slug}."
    )
    if confirmation_text.strip() != expected:
        raise DashboardServiceError(
            "Cross-owner confirmation text did not match the explicit prompt."
        )
    confirmation = storage.insert(
        "cross_owner_confirmations",
        {
            "actor_id": user.user_id,
            "actor_owner": user.owner,
            "target_owner": target_owner,
            "action": "generate_draft",
            "target_slug": target_slug,
            "confirmation_text": confirmation_text.strip(),
        },
        user.access_token,
    )[0]
    context = storage.rpc(
        "cross_owner_draft_context",
        {"p_confirmation_id": confirmation["id"]},
        user.access_token,
    )
    target = context["target"]
    artifact = context.get("artifact")
    contact = _contact_record(context["contact"]) if context.get("contact") else None
    record = _generate_record(target, artifact, contact, paragraph)
    draft_id = storage.rpc(
        "save_cross_owner_draft",
        {"p_confirmation_id": confirmation["id"], "p_payload": record},
        user.access_token,
    )
    return {
        "id": draft_id,
        "owner": record["owner"],
        "firm": record["firm"],
        "status": "pending_review",
        "confirmation_id": confirmation["id"],
        "note": "Saved into the target owner's RLS lane.",
    }


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
        user.access_token,
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
        "mark_draft_sent", {"p_draft_id": draft_id}, user.access_token
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
        user.access_token,
    )
    return dict(result or {})


def dashboard_state(
    storage: SupabaseStorage, user: DashboardUser
) -> dict[str, Any]:
    targets = storage.select(
        "targets", user.access_token, params={"order": "priority.asc,firm.asc"}
    )
    research = storage.select("research_artifacts", user.access_token)
    contacts = storage.select(
        "contacts", user.access_token,
        params={"dropped": "eq.false", "order": "verification_score.desc.nullslast"},
    )
    drafts = storage.select(
        "drafts", user.access_token, params={"order": "generated_at.desc"}
    )
    manual = storage.select(
        "manual_queue", user.access_token,
        params={"resolved_at": "is.null", "order": "queued_at.desc"},
    )
    confirmations = storage.select(
        "cross_owner_confirmations", user.access_token,
        params={"order": "confirmed_at.desc", "limit": "20"},
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
    if any(
        not _owner_is(row, user.owner, "actor_owner")
        for row in confirmations
    ):
        raise DashboardServiceError(
            "Owner-scope violation blocked while loading confirmations."
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
        },
        "targets": targets_with_gate,
        "research": research,
        "contacts": contacts,
        "drafts": drafts,
        "manual_queue": manual,
        "cross_owner_confirmations": confirmations,
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
