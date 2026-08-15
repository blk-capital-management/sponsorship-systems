"""The two provenance namespaces, their guards, and URL normalization.

firm_claim_sources must stay citable. contact_provenance must stay internal.
The tests below are the reason a LinkedIn-scoped search string can be kept as
honest discovery evidence without it ever reaching a firm.
"""

import json
from pathlib import Path

import jsonschema
import pytest

from common.http import Page
from common.namespaces import (
    InternalOnlyLeakError,
    ProvenanceNamespaceError,
    assert_renderable,
    build_contact_provenance,
    normalize_contact_url,
    strip_default_port,
    validate_firm_claim_source,
)
from research import fetch

SCHEMA = json.loads(
    (Path(__file__).parent.parent / "research" / "schema.json").read_text(encoding="utf-8")
)

HUNTER_STYLE_URL = (
    "https://www.google.com/search?q=site:linkedin.com%20hannah%20dinardo%20bamfunds"
)


# ── Firm namespace rejects what it cannot cite ────────────────────────────────

@pytest.mark.parametrize("url", [
    HUNTER_STYLE_URL,
    "https://www.linkedin.com/in/someone",
    "https://uk.linkedin.com/company/example",
    "https://www.google.com/search?q=balyasny+campus+recruiting",
    "https://duckduckgo.com/?q=advent",
])
def test_search_and_linkedin_urls_are_not_firm_claim_sources(url):
    with pytest.raises(ProvenanceNamespaceError):
        validate_firm_claim_source(url)


@pytest.mark.parametrize("url", [
    "https://www.bamfunds.com/careers/internships",
    "https://sixthstreet.com/careers/#early-careers-programs",
])
def test_direct_firm_urls_are_accepted(url):
    assert validate_firm_claim_source(url) == url


def test_linkedin_url_in_firm_claim_sources_is_rejected_at_schema_level():
    """Acceptance: the schema, not just the helper, refuses it."""
    artifact = json.loads(
        (Path(__file__).parent.parent / "research" / "out" / "balyasny.json")
        .read_text(encoding="utf-8")
    )
    jsonschema.validate(artifact, SCHEMA)  # baseline: the real artifact is valid

    artifact["firm_claim_sources"] = [HUNTER_STYLE_URL]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(artifact, SCHEMA)


def test_linkedin_url_in_a_hook_is_rejected_at_schema_level():
    artifact = json.loads(
        (Path(__file__).parent.parent / "research" / "out" / "balyasny.json")
        .read_text(encoding="utf-8")
    )
    artifact["alignment_hooks"][0]["firm_claim_source"] = HUNTER_STYLE_URL
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(artifact, SCHEMA)


# ── Contact namespace keeps what the firm namespace refuses ───────────────────

def test_contact_provenance_accepts_a_linkedin_scoped_search_string():
    provenance = build_contact_provenance(HUNTER_STYLE_URL, HUNTER_STYLE_URL,
                                          method="pattern_inference", provider="hunter")
    assert provenance["discovery_url"] == HUNTER_STYLE_URL
    assert provenance["internal_only"] is True


def test_a_renderer_refuses_anything_marked_internal_only():
    payload = {"email_body": "Hi Hannah,", "contact_provenance":
               build_contact_provenance(HUNTER_STYLE_URL)}
    with pytest.raises(InternalOnlyLeakError):
        assert_renderable(payload, "email_body")


def test_the_guard_finds_the_flag_nested_deep():
    payload = {"a": [{"b": {"c": build_contact_provenance("https://example.com")}}]}
    with pytest.raises(InternalOnlyLeakError):
        assert_renderable(payload)


def test_a_clean_payload_passes_the_guard():
    assert_renderable({"email_body": "Hi Hannah,", "to": "hdinardo@bamfunds.com"})


# ── Normalization (addendum A1 to A3) ─────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("https://www.bamfunds.com:443/", "https://www.bamfunds.com/"),
    ("https://www.bamfunds.com:443/careers", "https://www.bamfunds.com/careers"),
    ("http://example.com:80/x", "http://example.com/x"),
    # A non-default port is meaningful and must survive.
    ("https://example.com:8443/x", "https://example.com:8443/x"),
    ("http://example.com:8080/x", "http://example.com:8080/x"),
    ("https://example.com/x", "https://example.com/x"),
    ("", ""),
])
def test_strip_default_port(raw, expected):
    assert strip_default_port(raw) == expected
    assert strip_default_port(strip_default_port(raw)) == expected  # idempotent


def test_contact_urls_also_decode_spaces():
    assert normalize_contact_url(HUNTER_STYLE_URL) == (
        "https://www.google.com/search?q=site:linkedin.com hannah dinardo bamfunds"
    )


def test_firm_urls_keep_their_percent_encoding():
    """Decoding a firm claim source could break the link a reviewer clicks."""
    url = "https://example.com/a%20b"
    assert strip_default_port(url) == url


class _StubFetcher:
    """Returns pages whose URLs carry an explicit :443, as the real crawl did."""

    def __init__(self):
        self.calls: list[str] = []

    def get(self, url: str, use_cache: bool = True) -> Page:
        self.calls.append(url)
        html = (
            "<html><head><title>Balyasny Asset Management</title></head><body>"
            "<h1>Balyasny Asset Management</h1>"
            "<p>Balyasny is a multi-strategy asset management firm.</p>"
            "<a href='/careers'>Careers</a></body></html>"
        )
        return Page(url="https://www.bamfunds.com:443/", status=200, html=html)


def test_a_url_captured_with_443_is_stored_without_it(monkeypatch):
    """Addendum A3: normalization happens at capture, not only in a migration."""
    target = {"firm": "Balyasny Asset Management", "domain": "bamfunds.com",
              "region": "US", "relationship": "prospect"}
    cfg = {"max_pages_per_firm": 0, "confidence": {}}

    crawled, texts, _blocking = fetch.crawl_firm(_StubFetcher(), target, cfg, refresh=False)

    assert crawled[0]["url"] == "https://www.bamfunds.com/"
    assert all(":443" not in page["url"] for page in crawled)
    assert all(":443" not in key for key in texts)
