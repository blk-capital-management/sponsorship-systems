"""Acceptance coverage for the Firm Library and independent CRM statuses."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app import app
from dashboard.auth import DashboardUser
from dashboard.crm import enrich_target, pipeline_visible
from dashboard.models import DraftRequest, MeetingNoteRequest, StatusOverrideRequest
from research.hook_ids import artifact_with_hook_ids, research_hook_id
from dashboard.services import (
    batch_status_override,
    create_meeting_note,
    dashboard_state,
    get_firm_detail,
    update_meeting_note,
    update_status_override,
)
from scripts.reconcile_crm import (
    SourceRow,
    choose_candidates,
    date_candidate,
    reconcile,
)
from scripts.seed_supabase import target_payload


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATION = PROJECT_ROOT / "supabase" / "migrations" / "006_crm_firm_library.sql"


@pytest.fixture
def user() -> DashboardUser:
    return DashboardUser(
        user_id="00000000-0000-0000-0000-000000000001",
        email="jamari@blkcapitalmanagement.org",
        owner="jamari",
        display_name="Jamari Myers",
        gmail_sender="jamari@blkcapitalmanagement.org",
        access_token="user-jwt",
        role="owner",
        actor_display_name="Jamari Myers",
        available_lanes=("jamari",),
    )


def test_relationship_and_pipeline_stage_are_independent_and_manual_wins() -> None:
    target = enrich_target({
        "relationship_status_auto": "Cold Prospect",
        "relationship_status_override": "Existing Partner",
        "pipeline_stage_auto": "Researching",
        "pipeline_active": True,
    })
    assert target["relationship_status_effective"] == "Existing Partner"
    assert target["pipeline_stage_effective"] == "Researching"
    assert target["relationship_status_source"] == "manual"
    assert target["pipeline_stage_source"] == "automatic"
    assert target["pipeline_visible"] is True


def test_clearing_override_returns_to_automatic_and_terminal_firms_can_reopen() -> None:
    automatic = enrich_target({
        "relationship_status_auto": "Cold Prospect",
        "relationship_status_override": None,
        "pipeline_stage_auto": "Researching",
        "pipeline_active": True,
    })
    assert automatic["relationship_status_effective"] == "Cold Prospect"

    reopened = {
        "relationship_status_auto": "Not Interested",
        "pipeline_stage_auto": "Closed / No Active Workflow",
        "pipeline_stage_override": "Re-engagement",
        "pipeline_active": True,
    }
    assert pipeline_visible(reopened) is True
    reopened["pipeline_stage_override"] = None
    assert pipeline_visible(reopened) is False
    assert pipeline_visible({
        "relationship_status_auto": "Expired / Former Partner",
        "pipeline_stage_auto": "Re-engagement", "pipeline_active": True,
    }) is True
    assert pipeline_visible({
        "relationship_status_auto": "Global Partner",
        "pipeline_stage_auto": "Closed / Partner", "pipeline_active": True,
    }) is False


def test_status_request_validates_each_controlled_vocabulary() -> None:
    request = StatusOverrideRequest(
        field="relationship_status", value="Global Partner", reason="Confirmed globally."
    )
    assert request.value == "Global Partner"
    reset = StatusOverrideRequest(field="pipeline_stage", clear=True)
    assert reset.value is None
    with pytest.raises(ValidationError, match="Unrecognized relationship status"):
        StatusOverrideRequest(field="relationship_status", value="Meeting Scheduled")
    with pytest.raises(ValidationError, match="must not include a value"):
        StatusOverrideRequest(field="pipeline_stage", value="Researching", clear=True)


class CrmMutationStorage:
    def __init__(self) -> None:
        self.targets = {
            "t1": {
                "id": "t1", "owner": "jamari", "firm": "Alpha Capital",
                "relationship_status_auto": "Cold Prospect",
                "pipeline_stage_auto": "Researching", "pipeline_active": True,
            },
            "t2": {
                "id": "t2", "owner": "jamari", "firm": "Beta Capital",
                "relationship_status_auto": "Cold Prospect",
                "pipeline_stage_auto": "Researching", "pipeline_active": True,
            },
        }
        self.rpcs: list[tuple[str, dict[str, Any]]] = []
        self.inserts: list[tuple[str, Any]] = []
        self.updates: list[tuple[str, Any]] = []

    def select(self, table, token, *, params=None, lane=None):
        if table != "targets":
            return []
        target_id = str((params or {}).get("id", "")).removeprefix("eq.")
        return [self.targets[target_id]] if target_id in self.targets else []

    def rpc(self, function, payload, token, *, lane=None):
        self.rpcs.append((function, payload))
        target = self.targets[payload["p_target_id"]]
        suffix = "relationship_status_override" if payload["p_field"] == "relationship_status" else "pipeline_stage_override"
        target[suffix] = None if payload["p_clear"] else payload["p_value"]
        return dict(target)

    def insert(self, table, rows, token, *, return_rows=True, lane=None):
        self.inserts.append((table, rows))
        if table == "action_runs":
            return [{"id": "run-1", **rows}]
        return []

    def update(self, table, values, token, *, params, return_rows=True, lane=None):
        self.updates.append((table, values))
        return []


def test_single_and_batch_status_changes_use_atomic_audit_rpc(user: DashboardUser) -> None:
    storage = CrmMutationStorage()
    single = update_status_override(
        storage, user, "t1", field="pipeline_stage", value="Meeting Scheduled",
        clear=False, reason="Call booked.",
    )
    assert single["pipeline_stage_effective"] == "Meeting Scheduled"
    assert storage.rpcs[0][1]["p_reason"] == "Call booked."

    results = batch_status_override(
        storage, user, ["t1", "t2"], field="relationship_status",
        value="Existing Partner", clear=False, reason="Signed partners.",
    )
    assert [row["relationship_status_effective"] for row in results] == [
        "Existing Partner", "Existing Partner",
    ]
    run = next(values for table, values in storage.updates if table == "action_runs")
    assert run["status"] == "completed"
    assert run["details"]["errors"] == []


class FirmDetailStorage:
    def __init__(self) -> None:
        self.target = {
            "id": "t1", "owner": "jamari", "firm": "Alpha Capital",
            "relationship_status_auto": "Cold Prospect",
            "pipeline_stage_auto": "Researching", "pipeline_active": True,
            "created_at": "2026-08-01T12:00:00+00:00",
        }
        self.note: dict[str, Any] | None = None

    def select(self, table, token, *, params=None, lane=None):
        if table == "targets":
            return [self.target]
        if table == "meeting_notes" and self.note:
            return [self.note]
        return []

    def insert(self, table, rows, token, *, return_rows=True, lane=None):
        assert table == "meeting_notes"
        self.note = {"id": "note-1", "created_at": "2026-08-20T12:00:00+00:00", **rows}
        return [self.note]

    def update(self, table, values, token, *, params, return_rows=True, lane=None):
        assert table == "meeting_notes"
        self.note = {**(self.note or {}), **values}
        return [self.note]


def test_meeting_notes_create_view_edit_and_join_firm_activity(user: DashboardUser) -> None:
    storage = FirmDetailStorage()
    created = create_meeting_note(
        storage, user, "t1", interaction_date=date(2026, 8, 20),
        interaction_type="Sponsor call", participants=["Ava", "Jamari"],
        notes="Discussed fall programming.", next_step="Send the prospectus.",
        follow_up_date=date(2026, 8, 27),
    )
    assert created["participants"] == ["Ava", "Jamari"]
    updated = update_meeting_note(
        storage, user, "note-1", interaction_date=date(2026, 8, 20),
        interaction_type="Sponsor call", participants=["Ava", "Jamari"],
        notes="Discussed fall programming and speaker options.",
        next_step="Send the revised prospectus.", follow_up_date=date(2026, 8, 28),
    )
    assert updated["follow_up_date"] == "2026-08-28"
    detail = get_firm_detail(storage, user, "t1")
    assert detail["meeting_notes"][0]["id"] == "note-1"
    assert any(event["type"] == "meeting_note" for event in detail["activity"])


def test_meeting_note_request_normalizes_participants_and_requires_notes() -> None:
    request = MeetingNoteRequest(
        interaction_date=date(2026, 8, 20), participants=[" Ava ", "Ava", " Jamari Myers "],
        notes="  Useful   call. ",
    )
    assert request.participants == ["Ava", "Jamari Myers"]
    assert request.notes == "Useful call."
    with pytest.raises(ValidationError):
        MeetingNoteRequest(interaction_date=date(2026, 8, 20), notes="   ")


def test_dashboard_state_keeps_active_and_inactive_firms_in_master_library(
    user: DashboardUser, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StateStorage:
        def select(self, table, token, *, params=None, lane=None, select="*"):
            if table == "targets":
                return [
                    {
                        "id": "active", "owner": "jamari", "firm": "Active Prospect",
                        "contact_status": "cold_prospect",
                        "relationship_status_auto": "Cold Prospect",
                        "pipeline_stage_auto": "Researching", "pipeline_active": True,
                        "updated_at": "2026-08-20T12:00:00+00:00",
                    },
                    {
                        "id": "closed", "owner": "jamari", "firm": "Global Partner",
                        "contact_status": "existing_partner",
                        "relationship_status_auto": "Global Partner",
                        "pipeline_stage_auto": "Closed / Partner", "pipeline_active": False,
                    },
                ]
            if table == "contacts":
                return [{
                    "id": "c1", "target_id": "active", "owner": "jamari",
                    "name": "Ava Recruiter", "created_at": "2026-08-21T12:00:00+00:00",
                }]
            if table == "research_artifacts":
                return [{
                    "id": "r1", "target_id": "closed", "owner": "jamari",
                    "researched_at": "2026-08-22T12:00:00+00:00",
                    "artifact": {"firm": "Global Partner", "alignment_hooks": []},
                }]
            return []

    monkeypatch.setattr(
        "dashboard.services.hunter_balance",
        lambda _storage, _user: {"used": 0, "available": 0, "remaining": 0},
    )
    result = dashboard_state(StateStorage(), user)
    assert result["counts"]["targets"] == 2
    assert result["counts"]["pipeline_targets"] == 1
    assert {target["firm"] for target in result["targets"]} == {
        "Active Prospect", "Global Partner",
    }
    active = next(target for target in result["targets"] if target["id"] == "active")
    closed = next(target for target in result["targets"] if target["id"] == "closed")
    assert active["primary_contact"] == "Ava Recruiter"
    assert active["last_activity"].startswith("2026-08-21")
    assert closed["pipeline_stage_effective"] == "Closed / Partner"
    assert closed["pipeline_visible"] is False


def test_reconciliation_obeys_authority_and_explicit_corrections() -> None:
    rows = [
        SourceRow("Archive", 8, "J.P. Morgan", {"Status": "Expired"}),
        SourceRow("Jamari", 9, "Acme", {"Status": "Active"}),
        SourceRow("Jamari (Updated)", 10, "Acme", {"Status": "Expired"}),
    ]
    chosen, _ = choose_candidates(rows[1:], "acme")
    assert chosen["relationship_status"].value == "Existing Partner"
    assert chosen["relationship_status"].sheet == "Jamari"

    result = reconcile(rows, [{
        "firm": "J.P. Morgan", "contact_status": "existing_partner",
        "relationship_status_auto": "Existing Partner",
        "pipeline_stage_auto": "Closed / Partner", "owner": "jamari",
    }])
    jpm = next(firm for firm in result["firms"] if firm["firm_key"] == "jpmorgan")
    assert jpm["proposed"]["relationship_status"] == "Not Interested"
    assert jpm["proposed"]["pipeline_stage"] == "Closed / No Active Workflow"
    explicit = [change for change in result["changes"] if change["firm"] == "J.P. Morgan"]
    assert {change["category"] for change in explicit} == {"explicit user override"}
    assert {change["confidence_issue"] for change in explicit} == {
        "explicit instruction; highest authority"
    }
    assert result["summary"]["database_mutations"] == 0


def test_seed_snapshot_preserves_independent_relationship_and_stage() -> None:
    payload = target_payload({
        "owner": "jamari", "firm": "Point72 Asset Management", "domain": "point72.com",
        "contact_status": "lapsed_partner", "relationship_status": "Not Renewing",
        "status": "New Lead", "notes": "Former sponsor; win-back",
    })
    assert payload["relationship_status_auto"] == "Not Renewing"
    assert payload["pipeline_stage_auto"] == "Re-engagement"
    assert payload["pipeline_active"] is True


def test_reconciliation_flags_duplicates_ambiguous_matches_and_ambiguous_dates() -> None:
    rows = [
        SourceRow("Pipeline & Leads", 5, "Core Industral", {"Stage": "New Lead"}),
        SourceRow("Pipeline & Leads", 6, "Core Industral", {"Stage": "Follow-Up"}),
    ]
    result = reconcile(rows, [{"firm": "Core Industrial", "owner": "jamari"}])
    assert result["summary"]["duplicates"] == 1
    assert result["summary"]["ambiguous_matches"] == 1
    assert result["summary"]["records_requiring_manual_review"] >= 1
    value, issue = date_candidate("2027")
    assert value == "2027"
    assert "exact date not inferred" in issue


def test_reconciliation_flags_equal_authority_tie_and_uses_known_record_id() -> None:
    tied = [
        SourceRow("Fola", 5, "Legacy Firm Name", {"ID": "S777", "Status": "Active"}),
        SourceRow(
            "EMEA (Daniel & Hajar)", 6, "Legacy Firm Name",
            {"ID": "S777", "Status": "Expired"},
        ),
    ]
    chosen, conflicts = choose_candidates(tied, "renamed firm")
    assert "relationship_status" not in chosen
    assert any("equal-authority tie" in conflict for conflict in conflicts)

    result = reconcile(tied[:1], [{
        "firm": "Renamed Firm", "relationship_record_id": "S777",
        "contact_status": "existing_partner", "owner": "fola",
    }])
    firm = next(item for item in result["firms"] if item["database_match"] == "Renamed Firm")
    assert firm["source_rows"] == ["Fola!r5"]
    assert result["summary"]["ambiguous_matches"] == 0


def test_migration_is_additive_audited_and_rls_scoped() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    for fragment in (
        "relationship_status_auto", "relationship_status_override",
        "pipeline_stage_auto", "pipeline_stage_override", "pipeline_active",
        "assigned_owner", "partnership_scope", "partnership_type",
        "last_touchpoint", "email_chain_notes", "contact_verified_status",
        "create table if not exists public.crm_audit_events",
        "create table if not exists public.meeting_notes",
        "alter table public.crm_audit_events enable row level security",
        "alter table public.meeting_notes enable row level security",
        "set_target_status_override", "set_target_pipeline_active",
        "create trigger targets_automatic_status_audit",
        "new.pipeline_stage_auto_source, '')",
        "drop policy if exists targets_delete_own",
        "revoke delete on public.targets from authenticated",
    ):
        assert fragment in sql
    assert "drop table public.targets" not in sql
    assert "delete from public.targets" not in sql
    assert "where id = p_target_id and owner = public.current_owner()" in sql


def test_firm_library_ui_and_cold_only_conference_copy_are_present() -> None:
    html = (PROJECT_ROOT / "public" / "index.html").read_text(encoding="utf-8")
    javascript = (PROJECT_ROOT / "public" / "app.js").read_text(encoding="utf-8")
    cold = (PROJECT_ROOT / "templates" / "cold_prospect.md").read_text(encoding="utf-8")
    recovery = (PROJECT_ROOT / "templates" / "recovery.md").read_text(encoding="utf-8")
    phrase = (
        "As we finalize our fall partner programming ahead of our "
        "{conference_dates} Fall Conference in {conference_city}"
    )
    assert 'id="view-library"' in html
    assert 'id="library-search"' in html
    assert 'id="batch-relationship-status"' in html
    assert "function renderFirmDetail" in javascript
    assert "librarySearch.trim().toLowerCase()" in javascript
    assert "libraryFilters.relationship" in javascript
    assert "libraryFilters.assetClass" in javascript
    assert "Return to automatic" in javascript
    assert 'class="draft-hook-checkbox"' in javascript
    assert "supporting_hook_ids: supportingHookIds" in javascript
    assert 'id="draft-provenance-summary"' in html
    assert 'id="draft-validation-error"' in html
    assert phrase in cold
    assert phrase not in recovery


def test_research_hook_ids_are_stable_api_decorations_not_schema_mutations() -> None:
    artifact = {
        "firm": "Citadel", "firm_slug": "citadel",
        "alignment_hooks": [{
            "text": "We offer this program to exceptional undergraduates.",
            "firm_claim_source": "https://www.citadel.com/programs/",
            "quote": "We offer this program to exceptional undergraduates.",
            "basis": "campus_or_early_careers_programs",
        }],
    }
    decorated = artifact_with_hook_ids(artifact)
    first = decorated["alignment_hooks"][0]["research_hook_id"]
    assert first == research_hook_id(artifact, artifact["alignment_hooks"][0])
    assert first == artifact_with_hook_ids(artifact)["alignment_hooks"][0]["research_hook_id"]
    assert "research_hook_id" not in artifact["alignment_hooks"][0]

    request = DraftRequest(
        target_id="target-1", supporting_hook_ids=[first],
        firm_specific_paragraph="Sentence one. Sentence two.",
    )
    assert request.supporting_hook_ids == [first]
    assert "source_url" not in DraftRequest.model_json_schema()["properties"]


def test_crm_routes_are_exposed_without_any_send_endpoint() -> None:
    paths = app.openapi()["paths"]
    assert "patch" in paths["/api/targets/{target_id}/status"]
    assert "post" in paths["/api/status-overrides/batch"]
    assert "get" in paths["/api/firms/{target_id}"]
    assert "post" in paths["/api/firms/{target_id}/meeting-notes"]
    assert "patch" in paths["/api/meeting-notes/{note_id}"]
    assert not any("send-email" in path or "gmail/send" in path for path in paths)
