"""Hunter scope, budget, and audit controls.

- Domain Search / Email Finder reject a domain not in targets.csv.
- contacts/discover.py never references Hunter's Discover endpoint.
- The per-run cap hard-stops the run rather than warning.
"""

import ast
import csv
from pathlib import Path

import pytest

from contacts.hunter_guard import (
    DEFAULT_MAX_CALLS_PER_RUN,
    HunterBudgetError,
    HunterGuard,
    HunterScopeError,
    load_target_domains,
    normalize_domain,
)

PROJECT_ROOT = Path(__file__).parent.parent


@pytest.fixture
def guard(tmp_path):
    return HunterGuard(
        max_calls=3,
        target_domains={"bamfunds.com", "sixthstreet.com"},
        usage_log=tmp_path / "hunter_usage.csv",
    )


# ── Scope: only domains already in targets.csv ────────────────────────────────

@pytest.mark.parametrize("endpoint", ["domain-search", "email-finder"])
def test_off_target_domain_is_rejected(guard, endpoint):
    with pytest.raises(HunterScopeError, match="no row in data/targets.csv"):
        guard.authorize(endpoint, "randomcompany.com")


@pytest.mark.parametrize("endpoint", ["domain-search", "email-finder"])
def test_targeted_domain_is_allowed(guard, endpoint):
    guard.authorize(endpoint, "bamfunds.com")


def test_www_and_case_variants_still_match(guard):
    guard.authorize("domain-search", "WWW.BamFunds.com")
    guard.authorize("domain-search", "  sixthstreet.com  ")


def test_rejection_is_logged_not_silent(guard):
    with pytest.raises(HunterScopeError):
        guard.authorize("domain-search", "randomcompany.com")

    rows = list(csv.DictReader(guard.usage_log.open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["domain"] == "randomcompany.com"
    assert rows[0]["credits"] == "0"
    assert "REJECTED" in rows[0]["note"]


def test_missing_domain_is_rejected(guard):
    with pytest.raises(HunterScopeError, match="requires a domain"):
        guard.authorize("domain-search", "")


def test_unknown_endpoint_is_rejected(guard):
    with pytest.raises(HunterScopeError, match="not permitted"):
        guard.authorize("some-other-endpoint", "bamfunds.com")


def test_discover_is_not_domain_scoped(guard):
    """Discover is the one endpoint allowed to surface an untargeted domain."""
    guard.authorize("discover", None)


def test_real_targets_file_loads():
    domains = load_target_domains()
    assert "bamfunds.com" in domains
    assert "sixthstreet.com" in domains
    assert "adventinternational.com" in domains


@pytest.mark.parametrize("raw,expected", [
    ("WWW.Acme.com", "acme.com"), (" acme.com ", "acme.com"), ("", ""),
])
def test_domain_normalization(raw, expected):
    assert normalize_domain(raw) == expected


# ── Budget: the cap stops the run ─────────────────────────────────────────────

def test_cap_raises_rather_than_warns(guard):
    for _ in range(3):
        guard.authorize("domain-search", "bamfunds.com")
        guard.record("domain-search", "bamfunds.com")

    with pytest.raises(HunterBudgetError, match="call cap reached"):
        guard.authorize("domain-search", "bamfunds.com")


def test_cap_counts_across_endpoints(guard):
    guard.authorize("domain-search", "bamfunds.com")
    guard.record("domain-search", "bamfunds.com")
    guard.authorize("email-finder", "sixthstreet.com")
    guard.record("email-finder", "sixthstreet.com")
    guard.authorize("email-verifier", "bamfunds.com")
    guard.record("email-verifier", "bamfunds.com")

    with pytest.raises(HunterBudgetError):
        guard.authorize("email-finder", "bamfunds.com")


def test_scope_is_checked_before_budget(guard):
    """An off-target call must be refused on scope even at the cap."""
    guard.calls_made = 99
    with pytest.raises(HunterScopeError):
        guard.authorize("domain-search", "randomcompany.com")


def test_default_cap_is_ten():
    assert DEFAULT_MAX_CALLS_PER_RUN == 10


def test_env_var_overrides_the_cap(monkeypatch):
    monkeypatch.setenv("MAX_HUNTER_CALLS_PER_RUN", "2")
    assert HunterGuard.from_settings().max_calls == 2


def test_settings_cap_is_used_without_the_env_var(monkeypatch):
    monkeypatch.delenv("MAX_HUNTER_CALLS_PER_RUN", raising=False)
    assert HunterGuard.from_settings().max_calls == 10


# ── Audit log ─────────────────────────────────────────────────────────────────

def test_every_call_is_logged_with_credits(guard):
    guard.authorize("domain-search", "bamfunds.com")
    guard.record("domain-search", "bamfunds.com")
    guard.authorize("email-finder", "sixthstreet.com")
    guard.record("email-finder", "sixthstreet.com")

    rows = list(csv.DictReader(guard.usage_log.open(encoding="utf-8")))
    assert [r["endpoint"] for r in rows] == ["domain-search", "email-finder"]
    assert all(r["timestamp"] and r["run_id"] for r in rows)
    assert guard.credits_spent == 2


def test_historical_credits_accumulate(tmp_path):
    log_path = tmp_path / "usage.csv"
    first = HunterGuard(max_calls=5, target_domains={"acme.com"}, usage_log=log_path)
    first.authorize("domain-search", "acme.com")
    first.record("domain-search", "acme.com")

    second = HunterGuard(max_calls=5, target_domains={"acme.com"}, usage_log=log_path)
    assert second.historical_credits() == 1
    assert second.calls_made == 0  # the cap is per run, the log is cumulative


def test_account_endpoint_costs_nothing(guard):
    guard.authorize("account", None)
    guard.record("account", None)
    assert guard.credits_spent == 0


# ── Discover must not appear in the Phase 3 contact path ──────────────────────

def _module_source(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize("module", [
    "contacts/discover.py",
    "contacts/verify.py",
    "contacts/patterns.py",
])
def test_phase3_modules_never_reference_discover_endpoint(module):
    """Prospecting is a different job and must not run inside contact lookup."""
    source = _module_source(module).lower()
    assert "api.hunter.io/v2/discover" not in source
    assert '"discover"' not in source.replace("discover_pattern", "")


def test_hunter_adapter_defines_no_discover_endpoint():
    source = _module_source("contacts/providers/hunter.py")
    assert "v2/discover" not in source


def test_discover_module_calls_only_scoped_endpoints():
    """Static check that the contact path reaches Hunter only via allowed names."""
    tree = ast.parse(_module_source("contacts/discover.py"))
    called = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "discover_companies" not in called
    assert "discover" not in called


# ── Rule 3: LinkedIn-derived sources are not usable provenance ────────────────

from contacts.providers.hunter import HunterProvider, is_linkedin_derived  # noqa: E402


@pytest.mark.parametrize("source", [
    {"domain": "linkedin.com", "uri": "https://www.linkedin.com/in/x"},
    {"domain": "google.com",
     "uri": "https://www.google.com/search?q=site:linkedin.com%20jane%20acme"},
    {"domain": "", "uri": "https://lnkd.in/abc"},
])
def test_linkedin_sources_are_detected(source):
    assert is_linkedin_derived(source)


@pytest.mark.parametrize("source", [
    {"domain": "acme.com", "uri": "https://acme.com/press/hire"},
    {"domain": "prnewswire.com", "uri": "https://prnewswire.com/x"},
])
def test_non_linkedin_sources_pass(source):
    assert not is_linkedin_derived(source)


def _provider(reject_linkedin=False):
    return HunterProvider(
        api_key="test",
        guard=HunterGuard(max_calls=5, target_domains={"acme.com"}),
        reject_linkedin=reject_linkedin,
    )


_LINKEDIN_ONLY = {"data": {"pattern": "{f}{last}", "emails": [
    {"value": "jdoe@acme.com", "first_name": "Jane", "last_name": "Doe",
     "sources": [{"domain": "linkedin.com",
                  "uri": "https://www.google.com/search?q=site:linkedin.com%20jane"}]},
]}}


def test_linkedin_sources_are_rejected_when_the_flag_is_on():
    """Strict mode: a pattern backed only by LinkedIn is treated as unsourced."""
    assert _provider(reject_linkedin=True)._parse_domain_search(
        "acme.com", _LINKEDIN_ONLY) is None


def test_linkedin_sources_are_accepted_when_the_flag_is_off():
    """Jamari's call, 2026-08-13: a licensed API is within rule 3."""
    entry = _provider(reject_linkedin=False)._parse_domain_search(
        "acme.com", _LINKEDIN_ONLY)
    assert entry is not None
    assert entry["pattern"] == "{f}{last}@acme.com"
    assert entry["observed_examples"] == 1


def test_shipped_config_matches_the_decision():
    """The default must stay explicit, not drift silently."""
    from common.config import load_settings

    cfg = load_settings()["contacts"]["hunter"]
    assert cfg["reject_linkedin_derived_sources"] is False
    assert "hr" in cfg["domain_search_departments"]


def test_strict_mode_keeps_non_linkedin_sources():
    payload = {"data": {"pattern": "{f}{last}", "emails": [
        {"value": "jdoe@acme.com", "first_name": "Jane", "last_name": "Doe",
         "sources": [{"domain": "linkedin.com", "uri": "https://linkedin.com/in/j"}]},
        {"value": "bsmith@acme.com", "first_name": "Bob", "last_name": "Smith",
         "sources": [{"domain": "acme.com", "uri": "https://acme.com/press/hire"}]},
    ]}}
    entry = _provider(reject_linkedin=True)._parse_domain_search("acme.com", payload)
    assert entry["observed_examples"] == 1
    assert entry["source_url"] == "https://acme.com/press/hire"
    assert "linkedin" not in str(entry).lower()


def test_department_filter_is_sent_to_the_api():
    """An unfiltered call burns the 10-result allowance on investment staff."""
    provider = HunterProvider(
        api_key="test",
        guard=HunterGuard(max_calls=5, target_domains={"acme.com"}),
        departments="hr,executive",
    )
    captured = {}

    def fake_call(endpoint_name, url, params, domain):
        captured.update(params)
        raise RuntimeError("stop before the network")

    provider._call = fake_call
    provider.discover_pattern("acme.com")
    assert captured["department"] == "hr,executive"
    assert captured["limit"] <= 10


def test_domain_search_limit_respects_the_free_plan():
    from contacts.providers.hunter import DOMAIN_SEARCH_LIMIT

    assert DOMAIN_SEARCH_LIMIT <= 10
