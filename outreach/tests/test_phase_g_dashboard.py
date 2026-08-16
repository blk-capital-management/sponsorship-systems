"""Phase G dashboard, auth, and owner-lane acceptance guards."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import app
from dashboard.auth import DashboardUser, current_user
from dashboard.models import IntakeFirm, ReviewRequest
from dashboard.services import (
    DashboardServiceError,
    dashboard_settings,
    dashboard_state,
    derive_status_batch,
    get_target,
    intake_targets,
    mark_draft_sent,
    research_batch,
)
from dashboard.storage import SupabaseSettings, SupabaseStorage
from scripts.seed_supabase import seed_drafts


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATION = PROJECT_ROOT / "supabase" / "migrations" / "001_phase_g_dashboard.sql"
LANE_MIGRATION = PROJECT_ROOT / "supabase" / "migrations" / "005_lane_switching.sql"


@pytest.fixture
def jamari() -> DashboardUser:
    return DashboardUser(
        user_id="00000000-0000-0000-0000-000000000001",
        email="jamari@blkcapitalmanagement.org",
        owner="jamari",
        display_name="Jamari Myers",
        gmail_sender="jamari@blkcapitalmanagement.org",
        access_token="jamari-user-jwt",
        role="owner",
        actor_display_name="Jamari Myers",
        available_lanes=("fola", "jamari"),
    )


class IntakeStorage:
    """Fake storage for intake.

    `existing` seeds rows that intake's dedupe pass should see, and `reject`
    maps a firm_slug to the error its insert should raise, which is how a
    unique-constraint collision reaches the service in production.
    """

    def __init__(
        self,
        existing: list[dict[str, Any]] | None = None,
        reject: dict[str, str] | None = None,
    ) -> None:
        self.inserts: list[tuple[str, Any, str, bool]] = []
        self.existing = existing or []
        self.reject = reject or {}
        self._next = 0

    def select(
        self, table: str, token: str, *, params: Any = None, lane: str | None = None
    ) -> list[dict[str, Any]]:
        return list(self.existing) if table == "targets" else []

    def insert(
        self,
        table: str,
        rows: Any,
        token: str,
        *,
        return_rows: bool = True,
        lane: str | None = None,
    ) -> list[dict[str, Any]]:
        self.inserts.append((table, rows, token, return_rows))
        if table == "targets":
            # Intake inserts one row at a time so a single collision cannot
            # discard the rest of the batch.
            assert isinstance(rows, dict), "intake must insert targets one row at a time"
            message = self.reject.get(rows["firm_slug"])
            if message:
                raise RuntimeError(message)
            self._next += 1
            return [{"id": f"target-{self._next - 1}", **rows}]
        if table == "action_runs":
            return [{"id": "run-1", **rows}] if return_rows else []
        return [{"id": "queue-1", **rows}] if return_rows else []


def test_batch_intake_forces_authenticated_owner(jamari: DashboardUser) -> None:
    storage = IntakeStorage()
    result = intake_targets(
        storage,
        jamari,
        [IntakeFirm(firm="Example Capital", domain="example.com")],
    )

    rows = result["accepted"]
    assert rows[0]["owner"] == "jamari"
    assert rows[0]["created_by"] == jamari.user_id
    target_insert = storage.inserts[0]
    assert target_insert[0] == "targets"
    assert target_insert[2] == jamari.access_token


def test_intake_keeps_good_rows_when_one_firm_is_already_known(
    jamari: DashboardUser,
) -> None:
    """One duplicate must not discard the rest of a sourced batch."""
    storage = IntakeStorage(existing=[
        {"firm": "Known Capital", "firm_slug": "known", "domain": "known.com"},
    ])
    result = intake_targets(storage, jamari, [
        IntakeFirm(firm="Alpha Partners", domain="alpha.com"),
        IntakeFirm(firm="Known Capital", domain="known.com"),
        IntakeFirm(firm="Beta Capital", domain="beta.com"),
        IntakeFirm(firm="Alpha Partners", domain="alpha2.com"),
        IntakeFirm(firm="Gamma Group", domain="known.com"),
    ])

    assert [row["firm"] for row in result["accepted"]] == [
        "Alpha Partners", "Beta Capital",
    ]
    reasons = {item["firm"]: item["reason"] for item in result["skipped"]}
    assert reasons["Known Capital"] == "Already in your pipeline."
    assert reasons["Alpha Partners"] == "Duplicate firm in this batch."
    assert "known.com" in reasons["Gamma Group"]


def test_intake_reports_storage_rejection_per_row(jamari: DashboardUser) -> None:
    """A unique-constraint collision skips one row, not the batch."""
    storage = IntakeStorage(reject={
        # firm_slug drops generic suffixes, so "Beta Capital" keys on "beta".
        "beta": 'duplicate key value violates unique constraint "targets_firm_slug_key"',
    })
    result = intake_targets(storage, jamari, [
        IntakeFirm(firm="Alpha Partners", domain="alpha.com"),
        IntakeFirm(firm="Beta Capital", domain="beta.com"),
        IntakeFirm(firm="Gamma Group", domain="gamma.com"),
    ])

    assert [row["firm"] for row in result["accepted"]] == ["Alpha Partners", "Gamma Group"]
    assert result["skipped"] == [
        {"firm": "Beta Capital", "reason": "A firm with this slug already exists."},
    ]


def test_intake_persists_the_new_sourcing_fields(jamari: DashboardUser) -> None:
    storage = IntakeStorage()
    result = intake_targets(storage, jamari, [
        IntakeFirm(
            firm="Example Capital",
            website="https://www.example.com/about",
            linkedin_url="https://www.linkedin.com/company/example",
            firm_type="private equity",
            email_format="{f}{last}@example.com",
            email_format_source_url="https://example.com/team",
        ),
    ])

    row = result["accepted"][0]
    # domain is derived from the website, which is what sourcing turns up first
    assert row["domain"] == "example.com"
    assert row["website"] == "https://www.example.com/about"
    assert row["linkedin_url"] == "https://www.linkedin.com/company/example"
    assert row["firm_type"] == "PE"  # alias folded onto the controlled vocabulary
    assert row["email_format"] == "{f}{last}@example.com"
    assert row["email_format_source_url"] == "https://example.com/team"
    assert result["skipped"] == []


def test_intake_warns_on_near_duplicate_and_unknown_category(
    jamari: DashboardUser,
) -> None:
    """Both are warnings. The operator decides, and the lead is still added.

    firm_slug already collides "Sixth Street Partners" with "Sixth Street" by
    design, so those are caught as hard duplicates. The fuzzy pass covers what
    slugging does not fold, such as the abbreviation in "Meridian Intl".
    """
    storage = IntakeStorage(existing=[
        {"firm": "Meridian Intl", "firm_slug": "meridian_intl",
         "domain": "meridian-intl.com"},
    ])
    result = intake_targets(storage, jamari, [
        IntakeFirm(firm="Meridian", domain="meridian.com", firm_type="Crypto Fund"),
    ])

    assert len(result["accepted"]) == 1
    warnings = " ".join(item["warning"] for item in result["warnings"])
    assert "Meridian Intl" in warnings
    assert "Crypto Fund" in warnings
    assert result["accepted"][0]["firm_type"] == "Crypto Fund"


def test_intake_rejects_an_email_format_with_no_source() -> None:
    """Rule 1 for patterns: unsourced is not weaker, it is not a pattern."""
    with pytest.raises(ValidationError, match="email_format_source_url is required"):
        IntakeFirm(firm="Example Capital", domain="example.com",
                   email_format="{f}{last}@example.com")


def test_intake_rejects_an_unusable_email_format() -> None:
    with pytest.raises(ValidationError, match="not a usable pattern"):
        IntakeFirm(firm="Example Capital", domain="example.com",
                   email_format="{bogus}@example.com",
                   email_format_source_url="https://example.com/team")


def test_intake_rejects_a_non_linkedin_url_in_the_linkedin_field() -> None:
    with pytest.raises(ValidationError, match="linkedin.com URL"):
        IntakeFirm(firm="Example Capital", linkedin_url="https://twitter.com/example")


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
        lane: str | None = None,
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
        lane: str | None = None,
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
        lane: str | None = None,
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


# ── Phase G.2: lane switching ──────────────────────────────────────────────


def test_headers_carry_the_active_lane_for_caller_scoped_requests() -> None:
    storage = SupabaseStorage(
        SupabaseSettings(url="https://project.supabase.co", publishable_key="pub")
    )
    headers = storage._headers(token="user-jwt", lane="fola")
    assert headers["X-Blk-Lane"] == "fola"


def test_headers_omit_the_lane_when_none_is_given() -> None:
    storage = SupabaseStorage(
        SupabaseSettings(url="https://project.supabase.co", publishable_key="pub")
    )
    headers = storage._headers(token="user-jwt")
    assert "X-Blk-Lane" not in headers


def test_headers_omit_the_lane_for_service_role_requests_even_if_passed() -> None:
    storage = SupabaseStorage(
        SupabaseSettings(
            url="https://project.supabase.co",
            publishable_key="pub",
            secret_key="service-role-jwt",
        )
    )
    headers = storage._headers(service=True, lane="fola")
    assert "X-Blk-Lane" not in headers


def test_public_storage_methods_forward_lane_to_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for the contextvar-based design this replaced: it crashed
    with `ValueError: ... was created in a different Context` because FastAPI
    runs the two halves of a sync yield-dependency in separate threadpool
    dispatches, which do not share a contextvars.Context. lane is now passed
    as an ordinary keyword argument all the way down to _headers, with no
    global/thread-local state involved anywhere in the chain.
    """
    storage = SupabaseStorage(
        SupabaseSettings(url="https://project.supabase.co", publishable_key="pub")
    )
    seen_headers: list[dict[str, str]] = []

    class FakeResponse:
        status_code = 200
        content = b"[]"

        def json(self):
            return []

    def fake_request(method, url, *, headers, params, json, timeout):
        seen_headers.append(headers)
        return FakeResponse()

    monkeypatch.setattr("dashboard.storage.requests.request", fake_request)
    storage.select("targets", "user-jwt", lane="fola")
    storage.insert("targets", {"firm": "x"}, "user-jwt", lane="fola")
    storage.update("targets", {"firm": "x"}, "user-jwt", params={"id": "eq.1"}, lane="fola")
    storage.delete("targets", "user-jwt", params={"id": "eq.1"}, lane="fola")
    storage.rpc("some_fn", {}, "user-jwt", lane="fola")
    assert seen_headers and all(h.get("X-Blk-Lane") == "fola" for h in seen_headers)


class FakeAuthStorage:
    """Stands in for SupabaseStorage across the three calls current_user makes."""

    def __init__(
        self,
        profile: dict[str, Any],
        lane_access: list[dict[str, Any]],
        identity: dict[str, Any],
    ) -> None:
        self.profile = profile
        self.lane_access = lane_access
        self.identity = identity

    def auth_user(self, _token: str) -> dict[str, Any]:
        return {"id": self.profile["user_id"], "email": self.profile["email"]}

    def select(self, table: str, _token: str, *, params: Any = None) -> list[dict[str, Any]]:
        if table == "profiles":
            return [self.profile]
        if table == "profile_lane_access":
            return self.lane_access
        raise AssertionError(f"Unexpected select from {table}")

    def rpc(self, function: str, payload: dict[str, Any], _token: str) -> Any:
        assert function == "lane_identity"
        assert payload["p_lane"] in {"jamari", "fola"}
        return self.identity


JAMARI_IDENTITY = {"display_name": "Jamari Myers", "gmail_sender": "jamari@blkcapitalmanagement.org"}
FOLA_IDENTITY = {"display_name": "Folakunmi Awofisayo", "gmail_sender": "folakunmi@blkcapitalmanagement.org"}


def test_current_user_viewer_defaults_to_jamaris_lane_with_no_header() -> None:
    storage = FakeAuthStorage(
        profile={
            "user_id": "justin-id", "email": "justin@blkcapitalmanagement.org",
            "role": "viewer", "display_name": "Justin",
        },
        lane_access=[
            {"lane": "jamari", "is_default": True},
            {"lane": "fola", "is_default": False},
        ],
        identity=JAMARI_IDENTITY,
    )
    user = current_user(token="justin-jwt", storage=storage, x_blk_lane=None)
    assert user.owner == "jamari"
    assert user.role == "viewer"
    assert user.actor_display_name == "Justin"
    assert user.available_lanes == ("fola", "jamari")
    assert user.display_name == "Jamari Myers"


def test_current_user_switches_lane_via_header_when_permitted() -> None:
    storage = FakeAuthStorage(
        profile={
            "user_id": "jamari-id", "email": "jamari@blkcapitalmanagement.org",
            "role": "owner", "display_name": "Jamari Myers",
        },
        lane_access=[
            {"lane": "jamari", "is_default": True},
            {"lane": "fola", "is_default": False},
        ],
        identity=FOLA_IDENTITY,
    )
    user = current_user(token="jamari-jwt", storage=storage, x_blk_lane="fola")
    assert user.owner == "fola"
    assert user.gmail_sender == "folakunmi@blkcapitalmanagement.org"


def test_current_user_rejects_a_lane_the_account_does_not_hold() -> None:
    storage = FakeAuthStorage(
        profile={
            "user_id": "jamari-id", "email": "jamari@blkcapitalmanagement.org",
            "role": "owner", "display_name": "Jamari Myers",
        },
        lane_access=[{"lane": "jamari", "is_default": True}],
        identity=JAMARI_IDENTITY,
    )
    with pytest.raises(HTTPException) as excinfo:
        current_user(token="jamari-jwt", storage=storage, x_blk_lane="fola")
    assert excinfo.value.status_code == 403


def test_sql_lane_migration_leaves_owner_columns_and_checks_untouched() -> None:
    """Business-data ownership of targets/drafts/etc. must not change."""
    sql = LANE_MIGRATION.read_text(encoding="utf-8")
    assert "alter table public.targets" not in sql
    assert "alter table public.drafts add column" not in sql
    assert "alter table public.drafts drop column if exists cross_owner_confirmation_id" in sql


def test_sql_lane_migration_adds_the_two_new_viewer_accounts() -> None:
    sql = LANE_MIGRATION.read_text(encoding="utf-8")
    assert "justin@blkcapitalmanagement.org" in sql
    assert "belayneh@blkcapitalmanagement.org" in sql
    assert "role in ('owner', 'viewer')" in sql
    assert "owner is null or owner in ('jamari', 'fola')" in sql


def test_sql_lane_migration_grants_every_account_both_lanes_by_default() -> None:
    sql = LANE_MIGRATION.read_text(encoding="utf-8")
    assert "create table if not exists public.profile_lane_access" in sql
    assert "lane text not null check (lane in ('jamari', 'fola'))" in sql
    assert "profile_lane_access_select_self" in sql
    assert "coalesce(allowed.owner, 'jamari') = 'jamari'" in sql
    assert "coalesce(allowed.owner, 'jamari') = 'fola'" in sql


def test_sql_current_owner_validates_the_active_lane_header() -> None:
    sql = LANE_MIGRATION.read_text(encoding="utf-8")
    assert "current_setting('request.headers', true)::json->>'x-blk-lane'" in sql
    assert "is not permitted for this account" in sql


def test_sql_lane_migration_removes_the_cross_owner_confirmation_flow() -> None:
    sql = LANE_MIGRATION.read_text(encoding="utf-8")
    assert "drop table if exists public.cross_owner_confirmations cascade" in sql
    assert "drop function if exists public.save_cross_owner_draft(uuid, jsonb)" in sql
    assert "drop function if exists public.cross_owner_draft_context(uuid)" in sql


def test_dashboard_exposes_no_signup_or_send_route() -> None:
    paths = {route.path for route in app.routes}
    assert "/signup" not in paths
    assert "/api/signup" not in paths
    assert "/send" not in paths
    assert "/api/send" not in paths
    assert not any("send" in path.lower() for path in paths)


def test_unhandled_error_is_logged_in_full_but_never_echoed_to_the_client(
    jamari: DashboardUser,
) -> None:
    """A bug in an uncurated exception must not leak its message to the client
    (unlike the app's own custom exception types, whose messages are already
    reviewed and safe) -- only a request id the server log can be matched to.
    """
    from dashboard.auth import current_user as current_user_dep
    from dashboard.storage import get_storage as get_storage_dep

    secret_looking_message = "connection to postgres://svc:s3cr3t@db failed"

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(secret_looking_message)

    import app as app_module  # the module, distinct from `app` (the FastAPI instance)

    app.dependency_overrides[current_user_dep] = lambda: jamari
    app.dependency_overrides[get_storage_dep] = lambda: None
    original = app_module.dashboard_state
    app_module.dashboard_state = boom
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/state", headers={"Authorization": "Bearer t"})
    finally:
        app_module.dashboard_state = original
        app.dependency_overrides.pop(current_user_dep, None)
        app.dependency_overrides.pop(get_storage_dep, None)

    assert response.status_code == 500
    body = response.json()
    assert secret_looking_message not in body["detail"]
    assert "ref " in body["detail"]


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
    assert 'id="google-signin"' in index.text
    assert 'type="button" disabled' in index.text
    assert 'id="app-view" class="app-shell hidden" hidden' in index.text
    assert '/styles.css?v=20260816.4' in index.text
    assert '/app.js?v=20260816.4' in index.text
    assert "Recommended next step" in index.text
    assert "Build the evidence before the email" in index.text


def test_dashboard_login_waits_for_valid_public_config() -> None:
    javascript = (PROJECT_ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert "if (!data?.supabase_url || !data?.supabase_publishable_key)" in javascript
    assert '$("#google-signin").disabled = false' in javascript
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


def test_sql_auth_allowlist_contains_exactly_the_configured_accounts() -> None:
    sql = MIGRATION.read_text(encoding="utf-8") + "\n" + LANE_MIGRATION.read_text(encoding="utf-8")
    configured = set(re.findall(r"'([\w.\-]+@blkcapitalmanagement\.org)'", sql))
    assert configured == {
        "jamari@blkcapitalmanagement.org",
        "folakunmi@blkcapitalmanagement.org",
        "justin@blkcapitalmanagement.org",
        "belayneh@blkcapitalmanagement.org",
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


# The one-off cross-owner confirmation flow (see the original PR) was replaced
# by the always-available lane switcher; 001's original definition of it is
# left untouched as history, and 005 tears it down (see
# test_sql_lane_migration_removes_the_cross_owner_confirmation_flow above).


def test_browser_bundle_contains_login_only_and_no_secret_key_name() -> None:
    javascript = (PROJECT_ROOT / "public" / "app.js").read_text(encoding="utf-8")
    html = (PROJECT_ROOT / "public" / "index.html").read_text(encoding="utf-8")
    assert "provider=google" in javascript
    assert "grant_type=password" not in javascript
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
                    # Viewer profiles have owner = null; must not confuse the
                    # exactly-jamari-and-fola check below.
                    {"user_id": "justin-id", "owner": None},
                    {"user_id": "belayneh-id", "owner": None},
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

        def rpc(self, function, payload, token, *, lane=None):
            self.calls.append((function, payload, token))
            return {"id": "draft-1", "status": "sent"}

    storage = RpcStorage()
    result = mark_draft_sent(storage, jamari, "draft-1")
    assert result == {"id": "draft-1", "status": "sent"}
    assert storage.calls == [
        ("mark_draft_sent", {"p_draft_id": "draft-1"}, "jamari-user-jwt")
    ]


# ── Phase G.2: failure visibility, draft readiness, queue resolution ──────────

RESOLVE_MIGRATION = PROJECT_ROOT / "supabase" / "migrations" / "003_manual_queue_resolution.sql"


def test_hunter_balance_reports_why_it_is_unavailable(jamari) -> None:
    """A null balance that says nothing hid a broken service key for a day."""
    class BrokenStorage:
        def select(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            return [{"domain": "example.com"}]

        def service_insert(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("Invalid API key")

    from dashboard.services import hunter_balance

    import os as _os
    _os.environ.setdefault("HUNTER_API_KEY", "test-key")
    result = hunter_balance(BrokenStorage(), jamari)
    assert result["remaining"] is None
    assert result["error"], "a null balance must carry its cause"
    assert "service key" in result["error"] or "Invalid API key" in result["error"]


def test_dashboard_state_surfaces_balance_failure_without_breaking(jamari, monkeypatch) -> None:
    class OwnStorage:
        def select(self, table: str, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            if table == "targets":
                return [{"id": "t1", "owner": "jamari", "firm": "X",
                         "contact_status": "cold_prospect"}]
            return []

    def boom(*_args: Any, **_kwargs: Any):
        raise RuntimeError("Invalid API key")

    monkeypatch.setattr("dashboard.services.hunter_balance", boom)
    state = dashboard_state(OwnStorage(), jamari)
    assert state["hunter_balance"]["remaining"] is None
    assert "Invalid API key" in state["hunter_balance"]["error"]
    assert state["counts"]["targets"] == 1, "a provider fault must not empty the lane"


def test_state_exposes_the_daily_send_cap(jamari, monkeypatch) -> None:
    class OwnStorage:
        def select(self, table: str, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            return []

    monkeypatch.setattr("dashboard.services.hunter_balance",
                        lambda *_a, **_k: {"used": 0, "available": 60, "remaining": 60})
    counts = dashboard_state(OwnStorage(), jamari)["counts"]
    assert counts["daily_send_cap"] >= 1
    assert counts["sent_today"] == 0


def test_pipeline_explains_why_a_firm_cannot_be_drafted() -> None:
    javascript = (PROJECT_ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert "function draftBlocker(" in javascript
    assert "Cold prospects need a verified contact" in javascript
    assert "Cold prospects need research before drafting" in javascript


def test_manual_queue_resolution_requires_a_note_and_owns_the_lane() -> None:
    sql = RESOLVE_MIGRATION.read_text(encoding="utf-8")
    assert "create or replace function public.resolve_manual_queue_item" in sql
    assert "security definer" in sql
    assert "set search_path = public" in sql
    assert "owner = public.current_owner()" in sql
    assert "A resolution note is required" in sql
    assert "revoke execute on function public.resolve_manual_queue_item(uuid, text) from public, anon;" in sql
    assert "grant execute on function public.resolve_manual_queue_item(uuid, text) to authenticated;" in sql


def test_resolve_manual_request_rejects_a_blank_note() -> None:
    from dashboard.models import ResolveManualRequest

    assert ResolveManualRequest(note="  found by hand  ").note == "found by hand"
    with pytest.raises(ValidationError):
        ResolveManualRequest(note="   ")
