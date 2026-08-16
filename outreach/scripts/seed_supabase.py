"""Seed Phase G Supabase tables from the reviewed local artifacts.

The command is write-disabled unless `--yes` is supplied. It never modifies a
local file. The CRM workbook is opened read-only and its size and modification
time are checked before and after import.

Usage:
    python scripts/seed_supabase.py          # print the plan only
    python scripts/seed_supabase.py --yes    # perform idempotent imports
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.config import load_settings, resolve_path  # noqa: E402
from common.namespaces import build_contact_provenance  # noqa: E402
from common.slugify import firm_slug  # noqa: E402
from dashboard.storage import SupabaseSettings, SupabaseStorage  # noqa: E402
from scripts.derive_target_status import normalize_firm, read_crm  # noqa: E402


def as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"true", "yes", "y", "1"}


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def target_payload(row: dict[str, str]) -> dict[str, Any]:
    return {
        "owner": row["owner"].strip().lower(),
        "firm": row["firm"].strip(),
        "firm_slug": firm_slug(row["firm"]),
        "domain": row["domain"].strip(),
        "region": row.get("region", "").strip() or "US",
        "firm_type": row.get("firm_type", "").strip(),
        "tier_target": row.get("tier_target", "").strip(),
        "crm_status": row.get("status", "").strip(),
        "priority": as_int(row.get("priority"), 3),
        "relationship": row.get("relationship", "prospect").strip() or "prospect",
        "notes": row.get("notes", "").strip(),
        "contact_status": row.get("contact_status", "cold_prospect").strip(),
        "has_known_contact": as_bool(row.get("has_known_contact")),
        "contact_needs_refresh": as_bool(row.get("contact_needs_refresh")),
        "relationship_record_id": row.get("relationship_record_id", "").strip(),
        "relationship_tier": row.get("relationship_tier", "").strip(),
        "relationship_status": row.get("relationship_status", "").strip(),
        "relationship_contact_name": row.get("relationship_contact_name", "").strip(),
        "relationship_contact_email": row.get("relationship_contact_email", "").strip(),
        "relationship_expiration": row.get("relationship_expiration", "").strip() or None,
        "relationship_decline_reason": row.get("relationship_decline_reason", "").strip(),
        "relationship_crm_source": row.get("relationship_crm_source", "").strip(),
    }


def read_target_rows() -> list[dict[str, str]]:
    with (PROJECT_ROOT / "data" / "targets.csv").open(
        newline="", encoding="utf-8"
    ) as fh:
        return list(csv.DictReader(fh))


def seed_targets(
    storage: SupabaseStorage, rows: list[dict[str, str]]
) -> dict[str, dict[str, Any]]:
    result = storage.service_upsert(
        "targets", [target_payload(row) for row in rows],
        on_conflict="firm_slug", return_rows=True,
    )
    return {str(row["firm_slug"]): row for row in result}


def seed_crm(storage: SupabaseStorage) -> tuple[int, bool]:
    settings = load_settings()
    path = resolve_path(settings["crm"]["xlsx"]["path"])
    before = path.stat()
    rows, _skipped = read_crm(path)
    payload = [{
        "firm": row.firm,
        "firm_normalized": normalize_firm(row.firm),
        "tab": row.tab,
        "row_number": row.row_number,
        "status": row.status,
        "expiration_raw": str(row.expiration_raw or ""),
        "expiration": row.expiration.isoformat() if row.expiration else None,
        "contacts": row.contacts,
        "emails": row.emails,
        "is_ledger": row.is_ledger,
        "record_id": row.record_id,
        "tier": row.tier,
        "decline_reason": row.decline_reason,
    } for row in rows]
    if payload:
        storage.service_upsert(
            "crm_records", payload, on_conflict="tab,row_number", return_rows=False
        )
    after = path.stat()
    unchanged = (before.st_size, before.st_mtime) == (after.st_size, after.st_mtime)
    return len(payload), unchanged


def seed_research(
    storage: SupabaseStorage, targets: dict[str, dict[str, Any]]
) -> int:
    payload: list[dict[str, Any]] = []
    for path in sorted((PROJECT_ROOT / "research" / "out").glob("*.json")):
        artifact = json.loads(path.read_text(encoding="utf-8"))
        target = targets.get(artifact["firm_slug"])
        if not target:
            continue
        payload.append({
            "target_id": target["id"],
            "owner": target["owner"],
            "confidence": artifact["confidence"],
            "hook_count": len(artifact["alignment_hooks"]),
            "artifact": artifact,
            "gaps": artifact.get("gaps", []),
            "researched_at": artifact["fetched_at"],
        })
    if payload:
        storage.service_upsert(
            "research_artifacts", payload, on_conflict="target_id", return_rows=False
        )
    return len(payload)


def seed_contacts(
    storage: SupabaseStorage, targets: dict[str, dict[str, Any]]
) -> int:
    payload: list[dict[str, Any]] = []
    for slug, target in targets.items():
        path = PROJECT_ROOT / "contacts" / "out" / f"{slug}_verified.csv"
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if not row.get("email", "").strip():
                    continue
                payload.append({
                    "target_id": target["id"],
                    "owner": target["owner"],
                    "name": row.get("name", "").strip(),
                    "title": row.get("title", "").strip(),
                    "title_rank": as_int(row.get("title_rank"), 0),
                    "email": row.get("email", "").strip(),
                    "pattern": row.get("pattern", "").strip(),
                    "pattern_confidence": (
                        float(row["pattern_confidence"])
                        if row.get("pattern_confidence", "").strip() else None
                    ),
                    "verification_score": as_int(row.get("verification_score"), 0),
                    "verification_status": row.get("verification_status", "").strip(),
                    "verification_provider": row.get("verification_provider", "").strip(),
                    "contact_provenance": build_contact_provenance(
                        row.get("contact_provenance_discovery_url", ""),
                        row.get("contact_provenance_pattern_url", ""),
                        method="pattern_inference" if row.get("pattern") else "provider_published",
                        provider=row.get("verification_provider", ""),
                    ),
                    "dropped": False,
                })
    if payload:
        storage.service_upsert(
            "contacts", payload, on_conflict="target_id,email,name", return_rows=False
        )
    return len(payload)


def seed_drafts(
    storage: SupabaseStorage, targets: dict[str, dict[str, Any]]
) -> int:
    profiles = storage.service_select("profiles", select="user_id,owner")
    # Viewer profiles (Justin, Belayneh) have owner = null; only owner profiles
    # seed drafts, since only owner lanes hold data to seed against.
    user_ids = {str(row["owner"]): str(row["user_id"]) for row in profiles if row.get("owner")}
    if set(user_ids) != {"jamari", "fola"}:
        raise RuntimeError(
            "Exactly the Jamari and Fola Auth profiles must exist before draft seeding."
        )
    count = 0
    for path in sorted((PROJECT_ROOT / "review" / "drafts").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        target = targets.get(record["firm_slug"])
        if not target:
            continue
        is_approved = record["firm_slug"] == "balyasny"
        existing = storage.service_select(
            "drafts", select="id,status,approved_at,created_by",
            params={
                "target_id": f"eq.{target['id']}",
                "generated_at": f"eq.{record['generated_at']}",
                "limit": "1",
            },
        )
        if existing:
            draft_id = str(existing[0]["id"])
            updates: dict[str, Any] = {}
            if not existing[0].get("created_by"):
                updates["created_by"] = user_ids[record["owner"]]
            if (
                is_approved
                and existing[0].get("status") == "pending_review"
                and not existing[0].get("approved_at")
            ):
                updates.update({
                    "status": "approved",
                    "approved_by": user_ids[record["owner"]],
                    "approved_at": record["generated_at"],
                })
            if updates:
                storage.service_update(
                    "drafts", updates, params={"id": f"eq.{draft_id}"},
                    return_rows=False,
                )
        else:
            inserted = storage.service_insert("drafts", {
                "target_id": target["id"],
                "owner": record["owner"],
                "created_by": user_ids[record["owner"]],
                "firm": record["firm"],
                "firm_slug": record["firm_slug"],
                "contact_status": record["contact_status"],
                "contact": record["contact"],
                "subject": record.get("subject"),
                "subject_status": record["subject_status"],
                "email_body": record["email_body"],
                "evidence_block": record["evidence_block"],
                "validator_results": record["validator_results"],
                "fields": record["fields"],
                "firm_specific_paragraph": record.get("fields", {}).get(
                    "firm_specific_paragraph"
                ),
                "status": "approved" if is_approved else "pending_review",
                "approved_by": user_ids[record["owner"]] if is_approved else None,
                "approved_at": record["generated_at"] if is_approved else None,
                "generated_at": record["generated_at"],
            }, return_rows=True)
            draft_id = str(inserted[0]["id"])
            count += 1

        if is_approved:
            events = storage.service_select(
                "review_events",
                select="id",
                params={
                    "draft_id": f"eq.{draft_id}",
                    "action": "eq.approved",
                    "limit": "1",
                },
            )
            if not events:
                storage.service_insert(
                    "review_events",
                    {
                        "draft_id": draft_id,
                        "owner": record["owner"],
                        "actor_id": user_ids[record["owner"]],
                        "action": "approved",
                        "reason": "Imported from the existing human-approved Balyasny draft.",
                        "created_at": record["generated_at"],
                    },
                    return_rows=False,
                )
    return count


def seed_manual_queue(
    storage: SupabaseStorage, targets: dict[str, dict[str, Any]]
) -> int:
    path = PROJECT_ROOT / "review" / "manual_queue.csv"
    if not path.exists():
        return 0
    count = 0
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            target = targets.get(row["firm_slug"])
            if not target:
                continue
            existing = storage.service_select(
                "manual_queue", select="id",
                params={
                    "target_id": f"eq.{target['id']}",
                    "source_stage": "eq.research",
                    "resolved_at": "is.null",
                    "limit": "1",
                },
            )
            if existing:
                continue
            storage.service_insert("manual_queue", {
                "target_id": target["id"],
                "owner": target["owner"],
                "firm": row["firm"],
                "firm_slug": row["firm_slug"],
                "domain": row["domain"],
                "confidence": row["confidence"],
                "reason": row["reason"],
                "gaps": [value.strip() for value in row["gaps"].split(";") if value.strip()],
                "source_stage": "research",
                "queued_at": row["queued_at"],
            }, return_rows=False)
            count += 1
    return count


def seed_hunter_usage(
    storage: SupabaseStorage, targets: dict[str, dict[str, Any]]
) -> int:
    path = PROJECT_ROOT / "contacts" / "hunter_usage.csv"
    domain_owners = {target["domain"].lower(): target["owner"] for target in targets.values()}
    count = 0
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            existing = storage.service_select(
                "hunter_usage", select="id",
                params={
                    "timestamp": f"eq.{row['timestamp']}",
                    "endpoint": f"eq.{row['endpoint']}",
                    "domain": f"eq.{row['domain']}",
                    "limit": "1",
                },
            )
            if existing:
                continue
            storage.service_insert("hunter_usage", {
                "owner": domain_owners.get(row["domain"].lower()),
                "timestamp": row["timestamp"],
                "endpoint": row["endpoint"],
                "domain": row["domain"],
                "credits": as_int(row["credits"]),
                "run_id": row["run_id"],
                "note": row["note"],
            }, return_rows=False)
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="Perform the external writes.")
    args = parser.parse_args(argv)

    target_rows = read_target_rows()
    research_count = len(list((PROJECT_ROOT / "research" / "out").glob("*.json")))
    draft_count = len(list((PROJECT_ROOT / "review" / "drafts").glob("*.json")))
    print(f"Plan: {len(target_rows)} targets, up to {research_count} artifacts, "
          f"and up to {draft_count} drafts.")
    if not args.yes:
        print("Nothing written. Re-run with --yes after applying the SQL migration.")
        return 0

    storage = SupabaseStorage(SupabaseSettings.from_environment())
    if not storage.settings.secret_key:
        print("SUPABASE_SECRET_KEY is required for seeding.", file=sys.stderr)
        return 2

    targets = seed_targets(storage, target_rows)
    crm_count, crm_unchanged = seed_crm(storage)
    counts = {
        "targets": len(targets),
        "crm_records": crm_count,
        "research_artifacts": seed_research(storage, targets),
        "contacts": seed_contacts(storage, targets),
        "new_drafts": seed_drafts(storage, targets),
        "new_manual_queue": seed_manual_queue(storage, targets),
        "new_hunter_usage": seed_hunter_usage(storage, targets),
    }
    print(json.dumps(counts, indent=2))
    print(f"CRM workbook {'unchanged' if crm_unchanged else 'CHANGED'} after read-only import.")
    return 0 if crm_unchanged else 3


if __name__ == "__main__":
    raise SystemExit(main())
