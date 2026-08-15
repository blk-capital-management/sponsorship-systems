"""Phase G dashboard, auth, and owner-lane acceptance guards."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import app
from dashboard.auth import DashboardUser
from dashboard.models import IntakeFirm, ReviewRequest
from dashboard.services import (
    DashboardServiceError,
    dashboard_settings,
    dashboard_state,
    derive_status_batch,
    generate_cross_owner_draft,
    get_target,
    intake_targets,
    mark_draft_sent,
    research_batch,
)
from dashboard.storage import SupabaseSettings, SupabaseStorage
from scripts.seed_supabase import seed_drafts


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATION = PROJECT_ROOT / "supabase" / "migrations" / "001_phase_g_dashboard.sql"


@pytest.fixture
def jamari() -> DashboardUser:
    return DashboardUser(
        user_id="00000000-0000-0000-0000-000000000001",
        email="jamari@blkcapitalmanagement.org",
        owner="jamari",
        display_name="Jamari Myers",
        gmail_sender="jamari@blkcapitalmanagement.org",
        access_token="jamari-user-jwt",
    )


class IntakeStorage:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, Any, str, bool]] = []

    def insert(
        self,
        table: str,
        rows: Any,
        token: str,
        *,
        return_rows: bool = True,
    ) -> list[dict[str, Any]]:
        self.inserts.append((table, rows, token, return_rows))
        if table == "targets":
            return [{"id": f"target-{index}", **row} for index, row in enumerate(rows)]
        if table == "action_runs":
            return [{"id": "run-1", **rows}] if return_rows else []
        return [{"id": "queue-1", **rows}] if return_rows else []


def test_batch_intake_forces_authenticated_owner(jamari: DashboardUser) -> None:
    storage = IntakeStorage()
    rows = intake_targets(
        storage,
        jamari,
        [IntakeFirm(firm="Example Capital", domain="example.com")],
    )

    assert rows[0]["owner"] == "jamari"
    assert rows[0]["created_by"] == jamari.user_id
    target_insert = storage.inserts[0]
    assert target_insert[0] == "targets"
    assert target_insert[2] == jamari.access_token


def test_dashboard_uses_writable_ephemeral_cache_without_mutating_config() -> None:
    from common.config import load_settings

    original = load_settings()["research"]["cache_dir"]
    settings = dashboard_settings()
    assert settings["research"]["cache_dir"].startswith("/tmp/")
    assert load_settings()["research"]["cache_dir"] == original


def test_service_blocks_cross_owner_target_even_if_storage_leaks(
    jamari: DashboardUser,
) -> None:
    class LeakyStorage:
        def select(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            return [{"id": "fola-target", "owner": "fola"}]

    with pytest.raises(DashboardServiceError, match="owner lane"):
        get_target(LeakyStorage(), jamari, "fola-target")


def test_dashboard_state_blocks_cross_owner_rows(jamari: DashboardUser) -> None:
    class LeakyStorage:
        def select(self, table: str, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            if table == "targets":
                return [{"id": "fola-target", "owner": "fola"}]
            return []

    with pytest.raises(DashboardServiceError, match="Owner-scope violation"):
        dashboard_state(LeakyStorage(), jamari)


def test_dashboard_state_exposes_existing_hunter_gate_decision(
    jamari: DashboardUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OwnStorage:
        def select(self, table: str, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            if table == "targets":
                return [{
                    "id": "target-1",
                    "owner": "jamari",
                    "firm": "Known Partner",
                    "contact_status": "existing_partner",
                    "has_known_contact": True,
                    "contact_needs_refresh": False,
                }]
            return []

    monkeypatch.setattr(
        "dashboard.services.hunter_balance",
        lambda _storage, _user: {"used": 4, "available": 60, "remaining": 56},
    )
    result = dashboard_state(OwnStorage(), jamari)
    gate = result["targets"][0]["hunter_gate"]
    assert gate["skip"] is True
    assert gate["status"] == "skipped"
    assert "existing partner" in gate["reason"]


class BatchFailureStorage:
    def __init__(self) -> None:
        self.targets = {
            "target-1": {
                "id": "target-1",
                "owner": "jamari",
                "firm": "First Capital",
                "firm_slug": "first_capital",
                "domain": "first.example",
            },
            "target-2": {
                "id": "target-2",
                "owner": "jamari",
                "firm": "Second Capital",
                "firm_slug": "second_capital",
                "domain": "second.example",
            },
        }
        self.inserts: list[tuple[str, Any]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []

    def select(
        self,
        table: str,
        _token: str,
        *,
        select: str = "*",
        params: Any = None,
    ) -> list[dict[str, Any]]:
        if table == "targets":
            target_id = str((params or {}).get("id") or "").removeprefix("eq.")
            return [self.targets[target_id]] if target_id in self.targets else []
        if table == "manual_queue":
            return []
        raise AssertionError(f"Unexpected select from {table}")

    def insert(
        self,
        table: str,
        rows: Any,
        _token: str,
        *,
        return_rows: bool = True,
    ) -> list[dict[str, Any]]:
        self.inserts.append((table, rows))
        if table == "action_runs":
            return [{"id": "run-1", **rows}]
        return [{"id": "queue-1", **rows}] if return_rows else []

    def update(
        self,
        table: str,
        values: dict[str, Any],
        _token: str,
        *,
        params: Any,
        return_rows: bool = True,
    ) -> list[dict[str, Any]]:
        self.updates.append((table, values))
        return []


def test_research_batch_routes_failures_to_visible_manual_queue(
    jamari: DashboardUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = BatchFailureStorage()

    def fake_research_target(
        _storage: Any, _user: DashboardUser, target_id: str
    ) -> dict[str, Any]:
        if target_id == "target-1":
            raise RuntimeError("identity-checked crawl failed")
        return {
            "firm": "Second Capital",
            "confidence": "high",
            "alignment_hooks": [{"claim": "verified"}],
        }

    monkeypatch.setattr("dashboard.services.research_target", fake_research_target)
    results = research_batch(storage, jamari, ["target-1", "target-2"])

    assert any(row.get("target_id") == "target-2" and row.get("hooks") == 1 for row in results)
    assert any(row.get("target_id") == "target-1" and row.get("error") for row in results)
    queue = [row for table, row in storage.inserts if table == "manual_queue"]
    assert queue[0]["target_id"] == "target-1"
    assert queue[0]["source_stage"] == "research"
    assert queue[0]["reason"] == "Research failed and requires manual review."
    run = next(values for table, values in storage.updates if table == "action_runs")
    assert run["status"] == "failed"
    assert run["completed_at"]
    assert len(run["details"]["results"]) == 1
    assert len(run["details"]["errors"]) == 1


def test_status_batch_finalizes_and_preserves_success_when_one_target_fails(
    jamari: DashboardUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = BatchFailureStorage()

    def fake_derive_status(
        _storage: Any, _user: DashboardUser, target_id: str
    ) -> dict[str, Any]:
        if target_id == "target-1":
            raise RuntimeError("CRM snapshot lookup failed")
        return {**storage.targets[target_id], "contact_status": "cold_prospect"}

    monkeypatch.setattr("dashboard.services.derive_status", fake_derive_status)
    results = derive_status_batch(storage, jamari, ["target-1", "target-2"])

    assert any(row.get("target_id") == "target-1" and row.get("error") for row in results)
    assert any(row.get("id") == "target-2" for row in results)
    queue = [row for table, row in storage.inserts if table == "manual_queue"]
    assert queue[0]["source_stage"] == "derive_status"
    run = next(values for table, values in storage.updates if table == "action_runs")
    assert run["status"] == "failed"
    assert run["completed_at"]


def test_cross_owner_action_requires_exact_confirmation(
    jamari: DashboardUser,
) -> None:
    class NoWriteStorage:
        def insert(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            raise AssertionError("confirmation must be validated before any write")

    with pytest.raises(DashboardServiceError, match="did not match"):
        generate_cross_owner_draft(
            NoWriteStorage(),
            jamari,
            target_owner="fola",
            target_slug="example_capital",
            paragraph="Human paragraph.",
            confirmation_text="yes",
        )


def test_reject_request_requires_logged_reason() -> None:
    with pytest.raises(ValidationError, match="rejection reason"):
        ReviewRequest(action="rejected", reason="  ")
    approved = ReviewRequest(action="approved", reason=None)
    assert approved.reason is None


def test_opaque_secret_key_never_enters_authorization_header() -> None:
    storage = SupabaseStorage(
        SupabaseSettings(
            url="https://project.supabase.co",
            publishable_key="sb_publishable_public",
            secret_key="sb_secret_server_only",
        )
    )
    headers = storage._headers(service=True)
    assert headers["apikey"] == "sb_secret_server_only"
    assert "Authorization" not in headers


def test_legacy_service_role_jwt_still_uses_bearer_header() -> None:
    storage = SupabaseStorage(
        SupabaseSettings(
            url="https://project.supabase.co",
            publishable_key="anon-jwt",
            secret_key="service-role-jwt",
        )
    )
    headers = storage._headers(service=True)
    assert headers["Authorization"] == "Bearer service-role-jwt"


def test_user_requests_pair_publishable_key_with_user_jwt() -> None:
    storage = SupabaseStorage(
        SupabaseSettings(
            url="https://project.supabase.co",
            publishable_key="sb_publishable_public",
        )
    )
    headers = storage._headers(token="signed-user-jwt")
    assert headers["apikey"] == "sb_publishable_public"
    assert headers["Authorization"] == "Bearer signed-user-jwt"


def test_dashboard_exposes_no_signup_or_send_route() -> None:
    paths = {route.path for route in app.routes}
    assert "/signup" not in paths
    assert "/api/signup" not in paths
    assert "/send" not in paths
    assert "/api/send" not in paths
    assert not any("send" in path.lower() for path in paths)


def test_health_and_static_shell_are_served_with_security_headers() -> None:
    with TestClient(app) as client:
        health = client.get("/health")
        index = client.get("/")
    assert health.json() == {"status": "ok", "service": "blk-bridge"}
    assert health.headers["x-frame-options"] == "DENY"
    assert "https://*.supabase.co" in health.headers["content-security-policy"]
    assert health.headers["permissions-policy"] == (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    assert index.status_code == 200
    assert "no public signup" in index.text.lower()
    assert 'id="login-submit"' in index.text
    assert 'type="submit" disabled' in index.text
    assert 'id="app-view" class="app-shell hidden" hidden' in index.text
    assert '/styles.css?v=20260815.2' in index.text
    assert '/app.js?v=20260815.2' in index.text
    assert "Recommended next step" in index.text
    assert "Build the evidence before the email" in index.text


def test_dashboard_login_waits_for_valid_public_config() -> None:
    javascript = (PROJECT_ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert "if (!data?.supabase_url || !data?.supabase_publishable_key)" in javascript
    assert '$("#login-submit").disabled = false' in javascript
    assert "if (!config) throw new Error" in javascript
    assert "loginView.hidden = active" in javascript
    assert "appView.hidden = !active" in javascript


def test_sql_enables_rls_on_every_phase_g_table() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    tables = set(re.findall(r"create table if not exists public\.(\w+)", sql))
    rls_tables = set(
        re.findall(r"alter table public\.(\w+) enable row level security", sql)
    )
    assert tables == {
        "allowed_users",
        "profiles",
        "targets",
        "crm_records",
        "research_artifacts",
        "contacts",
        "manual_queue",
        "action_runs",
        "cross_owner_confirmations",
        "drafts",
        "review_events",
        "hunter_usage",
    }
    assert rls_tables == tables


def test_sql_auth_allowlist_contains_exactly_two_accounts() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    configured = set(
        re.findall(r"'([^']+@blkcapitalmanagement\.org)', '(?:jamari|fola)'", sql)
    )
    assert configured == {
        "jamari@blkcapitalmanagement.org",
        "folakunmi@blkcapitalmanagement.org",
    }
    assert "handle_blk_auth_user" in sql
    assert "permits only the configured BLK Bridge accounts" in sql


def test_sql_keeps_internal_tables_out_of_authenticated_data_api() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert (
        "revoke all on public.allowed_users, public.crm_records, public.hunter_usage\n"
        "  from authenticated;"
    ) in sql
    assert "revoke all on public.allowed_users, public.profiles, public.targets" in sql
    assert "from anon;" in sql
    assert "grant select on public.drafts to authenticated;" in sql
    assert "grant select, insert on public.drafts to authenticated;" not in sql


def test_sql_cross_owner_path_is_confirmed_expiring_and_one_time() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "actor_owner <> target_owner" in sql
    assert "confirmation_text = 'I confirm that ' || actor_owner" in sql
    assert "confirmed_at between now() - interval '1 minute'" in sql
    assert "confirmation_row.actor_owner <> public.current_owner()" in sql
    assert "confirmation_row.confirmed_at < now() - interval '10 minutes'" in sql
    assert "confirmation_row.context_accessed_at is not null" in sql
    assert "confirmation_row.consumed_at is not null" in sql
    assert "set consumed_at = now()" in sql


def test_browser_bundle_contains_login_only_and_no_secret_key_name() -> None:
    javascript = (PROJECT_ROOT / "public" / "app.js").read_text(encoding="utf-8")
    html = (PROJECT_ROOT / "public" / "index.html").read_text(encoding="utf-8")
    assert "grant_type=password" in javascript
    assert "signUp" not in javascript
    assert "/signup" not in javascript
    assert "SUPABASE_SECRET_KEY" not in javascript + html
    assert "no public signup" in html.lower()


def test_seed_preserves_balyasny_human_approval_identity_and_event() -> None:
    class SeedStorage:
        def __init__(self) -> None:
            self.inserts: list[tuple[str, dict[str, Any], bool]] = []

        def service_select(
            self, table: str, *, select: str = "*", params: Any = None
        ) -> list[dict[str, Any]]:
            if table == "profiles":
                return [
                    {"user_id": "jamari-id", "owner": "jamari"},
                    {"user_id": "fola-id", "owner": "fola"},
                ]
            return []

        def service_insert(
            self,
            table: str,
            rows: dict[str, Any],
            *,
            return_rows: bool = True,
        ) -> list[dict[str, Any]]:
            self.inserts.append((table, rows, return_rows))
            if table == "drafts":
                return [{"id": f"draft-{rows['firm_slug']}"}]
            return []

    targets: dict[str, dict[str, Any]] = {}
    for path in (PROJECT_ROOT / "review" / "drafts").glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        targets[record["firm_slug"]] = {
            "id": f"target-{record['firm_slug']}",
            "owner": record["owner"],
        }
    storage = SeedStorage()
    assert seed_drafts(storage, targets) == 4

    drafts = [row for table, row, _ in storage.inserts if table == "drafts"]
    balyasny = next(row for row in drafts if row["firm_slug"] == "balyasny")
    assert balyasny["created_by"] == "jamari-id"
    assert balyasny["status"] == "approved"
    assert balyasny["approved_by"] == "jamari-id"
    events = [row for table, row, _ in storage.inserts if table == "review_events"]
    assert events == [{
        "draft_id": "draft-balyasny",
        "owner": "jamari",
        "actor_id": "jamari-id",
        "action": "approved",
        "reason": "Imported from the existing human-approved Balyasny draft.",
        "created_at": balyasny["generated_at"],
    }]


# ── Phase G.1: subject lines and the human copy-out path ──────────────────────

SENT_MIGRATION = PROJECT_ROOT / "supabase" / "migrations" / "002_sent_tracking.sql"


def test_dashboard_javascript_subject_fallback_matches_generate_py() -> None:
    """The browser fallback for pre-templating drafts must not drift from Python."""
    from drafts.generate import SUBJECT_BY_STATUS

    javascript = (PROJECT_ROOT / "public" / "app.js").read_text(encoding="utf-8")
    for status, subject in SUBJECT_BY_STATUS.items():
        assert f'{status}: "{subject}"' in javascript


def test_copy_out_is_gated_on_human_approval() -> None:
    """A pending draft must not expose copy or compose controls."""
    javascript = (PROJECT_ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert 'if (draft.status === "pending_review") {' in javascript
    assert "Copy and compose actions unlock once you approve this draft." in javascript


def test_gmail_link_is_compose_only_and_never_a_send_call() -> None:
    """The compose URL prefills a window. Nothing in the client transmits (rule 2)."""
    javascript = (PROJECT_ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert "https://mail.google.com/mail/?" in javascript
    assert '"view=cm"' in javascript
    assert "gmail.googleapis.com" not in javascript
    assert "messages/send" not in javascript


def test_compose_url_encodes_spaces_as_percent_twenty() -> None:
    """URLSearchParams would encode a space as '+', which Gmail renders literally
    in the body. encodeURIComponent is the only safe choice here."""
    javascript = (PROJECT_ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert "encodeURIComponent(draft.email_body)" in javascript
    assert "new URLSearchParams" not in javascript


def test_sent_migration_adds_status_without_dropping_existing_ones() -> None:
    sql = SENT_MIGRATION.read_text(encoding="utf-8")
    for status in ("pending_review", "approved", "rejected", "gmail_created", "sent"):
        assert f"'{status}'" in sql
    assert "add column if not exists sent_at timestamptz" in sql
    assert "add column if not exists sent_by uuid references auth.users(id)" in sql


def test_mark_draft_sent_enforces_owner_lane_and_prior_approval() -> None:
    sql = SENT_MIGRATION.read_text(encoding="utf-8")
    assert "create or replace function public.mark_draft_sent(p_draft_id uuid)" in sql
    assert "security definer" in sql
    assert "set search_path = public" in sql
    assert "owner = public.current_owner()" in sql
    assert "for update" in sql
    assert "Only an approved draft may be marked as sent." in sql
    assert "revoke execute on function public.mark_draft_sent(uuid) from public, anon;" in sql
    assert "grant execute on function public.mark_draft_sent(uuid) to authenticated;" in sql


def test_mark_draft_sent_service_calls_the_rpc_under_the_caller_token(jamari) -> None:
    class RpcStorage:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, str]] = []

        def rpc(self, function, payload, token):
            self.calls.append((function, payload, token))
            return {"id": "draft-1", "status": "sent"}

    storage = RpcStorage()
    result = mark_draft_sent(storage, jamari, "draft-1")
    assert result == {"id": "draft-1", "status": "sent"}
    assert storage.calls == [
        ("mark_draft_sent", {"p_draft_id": "draft-1"}, "jamari-user-jwt")
    ]
