"""Contact-status routing: the pre-Hunter gate and the Phase 4 draft router.

Two separate protections that share one input. The gate stops credits being
spent on a firm we already have a contact for. The router stops a cold email
being drafted for a firm we already have a relationship with. Neither reads the
CRM; both read data/targets.csv.
"""

import csv

import pytest

from contacts.gate import evaluate_pre_hunter_gate
from contacts.hunter_guard import HunterGuard
from drafts.routing import (
    COLD_PROSPECT, DraftRoutingError, NEEDS_RECOVERY_CSV, NEEDS_WARM_CSV,
    route_target,
)


def _target(**overrides) -> dict:
    base = {
        "firm": "Sixth Street", "domain": "sixthstreet.com",
        "contact_status": "existing_partner", "has_known_contact": "TRUE",
        "contact_needs_refresh": "FALSE",
    }
    base.update(overrides)
    return base


# ── Pre-Hunter gate ───────────────────────────────────────────────────────────

def test_existing_partner_with_known_contact_skips_hunter():
    decision = evaluate_pre_hunter_gate(_target())
    assert decision.skip is True
    assert "no credit is spent" in decision.reason


def test_lapsed_partner_with_known_contact_also_skips_hunter():
    """A win-back goes to the contact who ran the last contract."""
    decision = evaluate_pre_hunter_gate(
        _target(firm="Point72 Asset Management", contact_status="lapsed_partner")
    )
    assert decision.skip is True
    assert "no credit is spent" in decision.reason


def test_lapsed_partner_flagged_for_refresh_still_proceeds_to_hunter():
    """HarbourVest and Select Equity's case: the contact on file has gone stale."""
    decision = evaluate_pre_hunter_gate(
        _target(contact_status="lapsed_partner", contact_needs_refresh="TRUE")
    )
    assert decision.skip is False
    assert "flagged for refresh" in decision.reason


def test_the_two_skip_reasons_are_distinguishable_in_the_usage_log():
    """Both land in hunter_usage.csv, so they must not read identically."""
    existing = evaluate_pre_hunter_gate(_target())
    lapsed = evaluate_pre_hunter_gate(_target(contact_status="lapsed_partner"))

    assert existing.skip is lapsed.skip is True
    assert existing.reason != lapsed.reason
    assert "existing partner" in existing.reason
    assert "lapsed partner" in lapsed.reason
    assert "contact_status=existing_partner" in existing.describe()
    assert "contact_status=lapsed_partner" in lapsed.describe()


@pytest.mark.parametrize("overrides", [
    {"contact_needs_refresh": "TRUE"},                      # flagged stale
    {"has_known_contact": "FALSE"},                         # nobody on file
    {"contact_status": "cold_prospect"},
    {"contact_status": "cold_prospect", "has_known_contact": "TRUE"},
    {"contact_status": "lapsed_partner", "has_known_contact": "FALSE"},
    {"contact_status": "lapsed_partner", "contact_needs_refresh": "TRUE"},
    {"contact_status": ""},                                 # not yet derived
    {"contact_status": "something_else"},
])
def test_every_other_combination_proceeds_to_hunter(overrides):
    assert evaluate_pre_hunter_gate(_target(**overrides)).skip is False


def test_the_skip_reason_carries_all_three_values():
    described = evaluate_pre_hunter_gate(_target()).describe()
    assert "contact_status=existing_partner" in described
    assert "has_known_contact=TRUE" in described
    assert "contact_needs_refresh=FALSE" in described


@pytest.mark.parametrize("status,domain", [
    ("existing_partner", "sixthstreet.com"),
    ("lapsed_partner", "point72.com"),
])
def test_the_gate_consumes_zero_credits(status, domain, tmp_path):
    """Acceptance: a firm with a known contact on file costs nothing to skip."""
    usage = tmp_path / "hunter_usage.csv"
    guard = HunterGuard(max_calls=10, target_domains={domain}, usage_log=usage)

    decision = evaluate_pre_hunter_gate(_target(contact_status=status))
    assert decision.skip is True
    guard.record_skip("domain-search", domain, note=f"SKIPPED: {decision.describe()}")

    assert guard.calls_made == 0
    assert guard.credits_spent == 0
    rows = list(csv.DictReader(usage.open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["credits"] == "0"
    assert "SKIPPED" in rows[0]["note"]
    assert f"contact_status={status}" in rows[0]["note"]


def test_discover_firm_skips_before_building_a_provider(monkeypatch):
    """The skip happens before anything is fetched, so nothing can be spent."""
    from contacts import discover

    monkeypatch.setattr(discover, "find_target", lambda firm, targets: _target())
    monkeypatch.setattr(discover, "load_targets", lambda: [])
    monkeypatch.setattr(discover, "log_skip", lambda decision, domain="": None)

    def explode(*args, **kwargs):
        raise AssertionError("a provider was built, so a credit was about to be spent")

    monkeypatch.setattr(discover, "Fetcher", explode)
    monkeypatch.setattr(discover, "write_contacts", explode)

    assert discover.discover_firm("Sixth Street") == []


# ── Phase 4 routing ───────────────────────────────────────────────────────────

def test_cold_prospect_is_the_only_status_that_drafts(tmp_path):
    assert route_target(_target(contact_status="cold_prospect", firm="Balyasny"),
                        slug="balyasny", review_dir=tmp_path) == COLD_PROSPECT


def test_existing_partner_raises_and_queues_one_warm_row(tmp_path):
    with pytest.raises(DraftRoutingError) as exc:
        route_target(_target(), slug="sixth_street", review_dir=tmp_path)

    assert exc.value.contact_status == "existing_partner"
    rows = list(csv.DictReader((tmp_path / NEEDS_WARM_CSV.name).open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["firm"] == "Sixth Street"
    assert not (tmp_path / NEEDS_RECOVERY_CSV.name).exists()


def test_lapsed_partner_queues_recovery_not_warm(tmp_path):
    with pytest.raises(DraftRoutingError):
        route_target(_target(firm="Point72 Asset Management",
                             contact_status="lapsed_partner"),
                     slug="point72", review_dir=tmp_path)

    rows = list(csv.DictReader((tmp_path / NEEDS_RECOVERY_CSV.name).open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["contact_status"] == "lapsed_partner"
    assert not (tmp_path / NEEDS_WARM_CSV.name).exists()


@pytest.mark.parametrize("status", ["", "   ", "unknown", "existing", "prospect"])
def test_an_unrecognized_status_raises_rather_than_defaulting_to_cold(status, tmp_path):
    """No silent fallthrough. Defaulting to cold is the failure being prevented."""
    with pytest.raises(DraftRoutingError) as exc:
        route_target(_target(contact_status=status), slug="x", review_dir=tmp_path)
    assert "not assumed" in str(exc.value) or "Nothing is assumed" in str(exc.value)


def test_the_router_never_writes_a_queue_row_for_a_cold_prospect(tmp_path):
    route_target(_target(contact_status="cold_prospect"), slug="x", review_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []
