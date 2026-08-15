"""Phase 3 acceptance tests.

- A pattern with no source_url is rejected.
- An address below the verification threshold is dropped, not just flagged.
- A firm with no research artifact produces zero contact rows rather than
  falling back to a guessed pattern.
- No code path reaches LinkedIn.
"""

import csv
import json

import pytest

from common.config import load_email_patterns
from common.http import BlockedHostError, Fetcher
from contacts import discover, patterns as pattern_lib, verify
from contacts.providers import ProviderNotConfigured, build_provider
from contacts.providers.base import VerificationProvider, VerificationResult


# ── A pattern with no source_url is rejected ──────────────────────────────────

def test_pattern_without_source_url_is_rejected():
    """An unsourced pattern is not a weak pattern. It is not a pattern."""
    assert pattern_lib.infer_pattern(
        "jane.doe@acme.com", "Jane Doe", "acme.com", source_url=""
    ) is None


def test_pattern_with_source_url_is_accepted():
    entry = pattern_lib.infer_pattern(
        "jane.doe@acme.com", "Jane Doe", "acme.com",
        source_url="https://acme.com/media-contacts/",
    )
    assert entry is not None
    assert entry["pattern"] == "{first}.{last}@acme.com"
    assert entry["source_url"] == "https://acme.com/media-contacts/"


def test_config_loader_drops_unsourced_entries(tmp_path):
    path = tmp_path / "email_patterns.json"
    path.write_text(json.dumps({"patterns": {
        "sourced.com": {"pattern": "{f}{last}@sourced.com", "confidence": 0.9,
                        "source_url": "https://sourced.com/press/"},
        "unsourced.com": {"pattern": "{f}{last}@unsourced.com", "confidence": 0.9},
        "blank.com": {"pattern": "{f}{last}@blank.com", "source_url": ""},
    }}), encoding="utf-8")

    loaded = load_email_patterns(str(path))
    assert set(loaded) == {"sourced.com"}


def test_shipped_patterns_all_carry_a_source_url():
    for domain, entry in load_email_patterns().items():
        assert entry.get("source_url", "").startswith("http"), domain


def test_role_inboxes_teach_nothing_about_personal_addresses():
    """careers@ says nothing about how the firm builds a person's address."""
    assert pattern_lib.infer_pattern(
        "careers@acme.com", "Jane Doe", "acme.com", "https://acme.com/contact/"
    ) is None


def test_address_on_a_different_domain_is_ignored():
    assert pattern_lib.infer_pattern(
        "jane.doe@otherfirm.com", "Jane Doe", "acme.com", "https://acme.com/contact/"
    ) is None


@pytest.mark.parametrize("address,name,expected", [
    ("jane.doe@acme.com", "Jane Doe", "{first}.{last}@acme.com"),
    ("jdoe@acme.com", "Jane Doe", "{f}{last}@acme.com"),
    ("janedoe@acme.com", "Jane Doe", "{first}{last}@acme.com"),
    ("jane_doe@acme.com", "Jane Doe", "{first}_{last}@acme.com"),
])
def test_known_formats_are_detected(address, name, expected):
    entry = pattern_lib.infer_pattern(address, name, "acme.com", "https://acme.com/x")
    assert entry["pattern"] == expected


@pytest.mark.parametrize("address,name", [
    ("a.stuart@acme.com", "Alexandria Stuart"),
    ("jane.doe@acme.com", "Jane Doe"),
    ("jdoe@acme.com", "Jane Doe"),
    ("janed@acme.com", "Jane Doe"),
])
def test_rendering_is_the_inverse_of_detection(address, name):
    """A detected pattern must reproduce the address it was learned from."""
    entry = pattern_lib.infer_pattern(address, name, "acme.com", "https://acme.com/x")
    first, last = pattern_lib.split_name(name)
    assert pattern_lib.render_address(entry["pattern"], first, last) == address


def test_agreement_across_sources_raises_confidence():
    one = pattern_lib.infer_pattern("jane.doe@acme.com", "Jane Doe", "acme.com",
                                    "https://acme.com/press/a")
    two = pattern_lib.infer_pattern("john.smith@acme.com", "John Smith", "acme.com",
                                    "https://acme.com/press/b")
    merged = pattern_lib.merge_observations([one, two])
    assert merged["observed_examples"] == 2
    assert merged["confidence"] > one["confidence"]


def test_conflicting_formats_lower_confidence():
    consistent = pattern_lib.merge_observations([
        pattern_lib.infer_pattern("jane.doe@acme.com", "Jane Doe", "acme.com", "https://a/1"),
        pattern_lib.infer_pattern("john.smith@acme.com", "John Smith", "acme.com", "https://a/2"),
    ])
    conflicting = pattern_lib.merge_observations([
        pattern_lib.infer_pattern("jane.doe@acme.com", "Jane Doe", "acme.com", "https://a/1"),
        pattern_lib.infer_pattern("john.smith@acme.com", "John Smith", "acme.com", "https://a/2"),
        pattern_lib.infer_pattern("bsmith@acme.com", "Bob Smith", "acme.com", "https://a/3"),
    ])
    assert conflicting["confidence"] < consistent["confidence"]
    assert conflicting["conflicting_formats"]


# ── Sub-threshold addresses are dropped, not flagged ──────────────────────────

class _ScriptedProvider(VerificationProvider):
    """Returns a preset score per address so thresholds can be tested exactly."""

    name = "scripted"

    def __init__(self, scores: dict[str, int]):
        super().__init__(api_key="test")
        self.scores = scores

    def verify(self, email: str) -> VerificationResult:
        score = self.scores.get(email, 0)
        return VerificationResult(email, score, "deliverable" if score else "unknown",
                                  self.name)


@pytest.fixture
def contact_rows(tmp_path, monkeypatch):
    """Point verify.py at a temp contacts dir with three rows of varying quality."""
    monkeypatch.setattr(verify, "OUT_DIR", tmp_path)
    rows = [
        {"name": "Good Contact", "title": "Campus Recruiting", "title_rank": "1",
         "email": "good@acme.com", "pattern": "{first}@acme.com",
         "pattern_confidence": "0.9", "pattern_source_url": "https://acme.com/x",
         "source_url": "https://acme.com/team"},
        {"name": "Weak Contact", "title": "Early Careers", "title_rank": "2",
         "email": "weak@acme.com", "pattern": "{first}@acme.com",
         "pattern_confidence": "0.9", "pattern_source_url": "https://acme.com/x",
         "source_url": "https://acme.com/team"},
        {"name": "No Address", "title": "Human Capital", "title_rank": "4",
         "email": "", "pattern": "", "pattern_confidence": "",
         "pattern_source_url": "", "source_url": "https://acme.com/team"},
    ]
    path = tmp_path / "acme_contacts.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return tmp_path


def test_below_threshold_address_is_dropped_not_flagged(contact_rows, monkeypatch):
    provider = _ScriptedProvider({"good@acme.com": 95, "weak@acme.com": 42})
    monkeypatch.setattr(verify, "build_provider", lambda settings: provider)
    monkeypatch.setattr(verify, "find_target",
                        lambda firm, targets: {"firm": "Acme", "domain": "acme.com",
                                               "owner": "jamari"})
    monkeypatch.setattr(verify, "load_targets", lambda: [])
    monkeypatch.setattr(verify, "load_settings", lambda: {
        "contacts": {"verification": {"provider": "scripted", "min_score": 80}}
    })

    kept, dropped = verify.verify_firm("Acme")

    kept_emails = {r["email"] for r in kept}
    assert kept_emails == {"good@acme.com"}
    # The weak address must be absent from the kept file entirely, not present
    # with a warning column.
    verified = list(csv.DictReader((contact_rows / "acme_verified.csv").open(encoding="utf-8")))
    assert {r["email"] for r in verified} == {"good@acme.com"}
    assert "weak@acme.com" not in (contact_rows / "acme_verified.csv").read_text()

    # Drops stay auditable rather than vanishing.
    reasons = {r["email"]: r["drop_reason"] for r in dropped}
    assert "below threshold" in reasons["weak@acme.com"]
    assert "no address" in reasons[""]


def test_dryrun_provider_drops_everything(contact_rows, monkeypatch):
    """A dry run must not mark unverified addresses as deliverable."""
    monkeypatch.setattr(verify, "find_target",
                        lambda firm, targets: {"firm": "Acme", "domain": "acme.com",
                                               "owner": "jamari"})
    monkeypatch.setattr(verify, "load_targets", lambda: [])
    monkeypatch.setattr(verify, "load_settings", lambda: {
        "contacts": {"verification": {"provider": "dryrun", "min_score": 80,
                                      "api_key_env": {}}}
    })
    kept, dropped = verify.verify_firm("Acme")
    assert kept == []
    assert len(dropped) == 3


def test_hunter_requires_a_key_from_the_environment(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    settings = {"contacts": {"verification": {
        "provider": "hunter", "min_score": 80,
        "api_key_env": {"hunter": "HUNTER_API_KEY"}}}}
    with pytest.raises(ProviderNotConfigured, match="HUNTER_API_KEY"):
        build_provider(settings)


def test_hunter_forces_undeliverable_to_zero():
    from contacts.providers.hunter import HunterProvider

    provider = HunterProvider(api_key="test")
    result = provider._parse("x@acme.com", {"data": {"result": "undeliverable",
                                                     "score": 66}})
    assert result.score == 0


def test_unknown_provider_is_rejected():
    with pytest.raises(ProviderNotConfigured, match="Unknown verification provider"):
        build_provider({"contacts": {"verification": {"provider": "nope"}}})


# ── No research artifact means zero contacts, never a guessed pattern ─────────

def test_missing_artifact_produces_zero_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(discover, "load_artifact", lambda slug, settings: None)
    monkeypatch.setattr(discover, "find_target", lambda firm, targets: {
        "firm": "Advent International", "domain": "adventinternational.com",
        "firm_type": "PE"})
    monkeypatch.setattr(discover, "load_targets", lambda: [])

    written = {}
    monkeypatch.setattr(discover, "write_contacts",
                        lambda slug, rows, settings: written.update({"rows": rows}))

    assert discover.discover_firm("Advent International") == []
    assert written["rows"] == []


def test_low_confidence_artifact_produces_zero_rows(monkeypatch):
    """Advent is manual-queued, so it must not acquire contacts by a side door."""
    monkeypatch.setattr(discover, "load_artifact", lambda slug, settings: {
        "confidence": "low", "alignment_hooks": []})
    monkeypatch.setattr(discover, "find_target", lambda firm, targets: {
        "firm": "Advent International", "domain": "adventinternational.com",
        "firm_type": "PE"})
    monkeypatch.setattr(discover, "load_targets", lambda: [])

    written = {}
    monkeypatch.setattr(discover, "write_contacts",
                        lambda slug, rows, settings: written.update({"rows": rows}))

    assert discover.discover_firm("Advent International") == []
    assert written["rows"] == []


def test_no_pattern_means_no_address_not_a_guess():
    """With no sourced pattern there is no fallback format to fall back to."""
    with pytest.raises(ValueError):
        pattern_lib.render_address("{first}.{last}", "Jane", "Doe", domain=None)


# ── Title priority ────────────────────────────────────────────────────────────

def test_human_capital_outranks_campus_titles_at_pe_firms():
    settings = {"contacts": {
        "title_priority": ["University Relations", "Campus Recruiting",
                           "Early Careers", "Emerging Talent", "Human Capital",
                           "Talent Partner", "Head of Talent Acquisition"],
        "human_capital_first_for_types": ["PE", "Private Credit"]}}

    assert discover.title_priority(settings, "PE")[0] == "Human Capital"
    assert discover.title_priority(settings, "Private Credit")[0] == "Human Capital"
    # At a hedge fund the campus titles stay on top.
    assert discover.title_priority(settings, "Multi-Strat HF")[0] == "University Relations"


def test_generic_hr_ranks_below_every_target_title():
    priority = ["University Relations", "Campus Recruiting", "Human Capital"]
    assert discover.rank_title("Head of Human Resources", priority) == len(priority)
    assert discover.rank_title("Campus Recruiting Lead", priority) == 1


def test_irrelevant_titles_are_not_targets():
    priority = ["Campus Recruiting", "Human Capital"]
    assert discover.rank_title("Managing Director, Investments", priority) is None
    assert discover.rank_title("Head of Talent, portfolio company", priority) is None


# ── Rule 3 ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/in/someone",
    "https://linkedin.com/company/acme",
    "https://lnkd.in/abc123",
])
def test_linkedin_is_refused_at_the_request_layer(url):
    fetcher = Fetcher({
        "user_agent": "test", "cache_dir": "/tmp/blk-test-cache",
        "requests_per_second_per_domain": 0,
    })
    with pytest.raises(BlockedHostError, match="rule 3"):
        fetcher.get(url)


# ── Licensed provider pattern lookup ──────────────────────────────────────────

def test_hunter_domain_search_requires_a_sourced_address():
    """A pattern Hunter cannot attribute to a public page is still unsourced."""
    from contacts.providers.hunter import HunterProvider

    provider = HunterProvider(api_key="test")
    payload = {"data": {"pattern": "{f}{last}",
                        "emails": [{"value": "jdoe@acme.com", "sources": []}]}}
    assert provider._parse_domain_search("acme.com", payload) is None


def test_hunter_domain_search_uses_the_real_source_url():
    from contacts.providers.hunter import HunterProvider

    provider = HunterProvider(api_key="test")
    payload = {"data": {"pattern": "{f}{last}", "emails": [
        {"value": "jdoe@acme.com", "first_name": "Jane", "last_name": "Doe",
         "position": "Campus Recruiting",
         "sources": [{"uri": "https://acme.com/press/hire"}]}]}}
    entry = provider._parse_domain_search("acme.com", payload)
    assert entry["pattern"] == "{f}{last}@acme.com"
    assert entry["source_url"] == "https://acme.com/press/hire"


def test_unrecognized_provider_pattern_is_rejected():
    from contacts.providers.hunter import HunterProvider

    provider = HunterProvider(api_key="test")
    payload = {"data": {"pattern": "{initials}{dept}", "emails": [
        {"value": "x@acme.com", "sources": [{"uri": "https://acme.com/p"}]}]}}
    assert provider._parse_domain_search("acme.com", payload) is None


def test_providers_without_lookup_return_none():
    """discover_pattern is opt-in, so a stub provider must not invent one."""
    from contacts.providers.stubs import DryRunProvider

    assert DryRunProvider().discover_pattern("acme.com") is None


@pytest.mark.parametrize("local", ["sixthstreetmedia", "example", "acmecareers",
                                   "talentteam", "noreply"])
def test_compound_role_inboxes_are_not_people(local):
    assert pattern_lib.is_role_address(local)


@pytest.mark.parametrize("local", ["jdoe", "jane.doe", "astuart"])
def test_personal_addresses_are_not_role_inboxes(local):
    assert not pattern_lib.is_role_address(local)
