"""Acceptance test 4: a low-confidence artifact routes to the manual queue and
produces no draft. Plus the extraction quality guards that keep hooks honest.
"""

import csv

import pytest

from common.config import load_settings
from research import extract, fetch


# ── Acceptance test 4: routing ────────────────────────────────────────────────

def _artifact(**overrides) -> dict:
    base = {
        "firm": "Test Firm", "firm_slug": "test", "domain": "test.com",
        "region": "US", "relationship": "prospect", "fetched_at": "2026-08-13",
        "pages_crawled": [], "alignment_hooks": [], "confidence": "low",
        "gaps": ["no careers page found"],
    }
    base.update(overrides)
    return base


def test_low_confidence_routes_to_manual_queue(tmp_path, monkeypatch):
    queue = tmp_path / "manual_queue.csv"
    settings = {"review": {"manual_queue_path": str(queue)}}
    monkeypatch.setattr(fetch, "resolve_path", lambda p, base=None: queue)

    fetch.route_to_manual_queue(_artifact(), settings)

    rows = list(csv.DictReader(queue.open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["firm"] == "Test Firm"
    assert rows[0]["confidence"] == "low"
    assert "no alignment hooks" in rows[0]["reason"]


def test_no_hooks_is_reported_as_the_reason(tmp_path, monkeypatch):
    queue = tmp_path / "manual_queue.csv"
    monkeypatch.setattr(fetch, "resolve_path", lambda p, base=None: queue)
    fetch.route_to_manual_queue(
        _artifact(confidence="high", alignment_hooks=[]),
        {"review": {"manual_queue_path": str(queue)}},
    )
    rows = list(csv.DictReader(queue.open(encoding="utf-8")))
    assert "no alignment hooks with a source_url" in rows[0]["reason"]


def test_blocking_reason_pins_confidence_to_low():
    """Volume of evidence about the wrong company is not confidence."""
    cfg = load_settings()["research"]
    rich = {"values_themes": [
        {"category": c, "value": "x", "source_url": "https://x.com", "quote": "x"}
        for c in ("careers", "values_culture", "news")
    ]}
    assert fetch.score_confidence(rich, cfg, []) == "high"
    assert fetch.score_confidence(rich, cfg, ["domain identity mismatch"]) == "low"


def test_blocked_artifact_carries_no_hooks():
    """Hooks are what drafting may assert, so a blocked artifact must emit none."""
    cfg = load_settings()["research"]
    target = {"firm": "Test Firm", "domain": "test.com", "region": "US",
              "relationship": "prospect"}
    texts = {"careers::https://test.com/careers":
             "We hire interns for our summer analyst program every year. " * 3}

    ok = fetch.build_artifact(target, [], texts, cfg, [])
    blocked = fetch.build_artifact(target, [], texts, cfg, ["domain identity mismatch"])

    assert ok["alignment_hooks"]
    assert blocked["alignment_hooks"] == []
    assert blocked["confidence"] == "low"
    assert "domain identity mismatch" in blocked["gaps"][0]


# ── Extraction quality ────────────────────────────────────────────────────────

def test_headings_do_not_glue_onto_sentences():
    """A heading merged into the next paragraph produces unreadable evidence."""
    html = (
        "<html><body><h1>Internships</h1>"
        "<p>We hire interns for roles on our Investment and Technology teams, "
        "seeking to convert top performers to full-time employees.</p>"
        "</body></html>"
    )
    found = extract.sentences(extract.page_text(html))
    assert any(s.startswith("We hire interns") for s in found)
    assert not any("Internships We hire" in s for s in found)


@pytest.mark.parametrize("junk", [
    "Learn More 3 / 25 In 2025, the firm invested in a European energy platform.",
    "An investment in these funds is speculative and involves a high degree of risk.",
    "SELECT INVESTMENTS In 2020, the firm completed a large royalty transaction.",
    "This website uses cookies to improve your experience on our site today.",
])
def test_boilerplate_is_never_evidence(junk):
    assert junk not in extract.sentences(junk)


def test_chrome_prefix_is_stripped_when_a_real_sentence_remains():
    text = ("Read more Balyasny hires interns across our Investment, Technology, "
            "and Business Infrastructure teams every year.")
    found = extract.sentences(text)
    assert found == ["Balyasny hires interns across our Investment, Technology, "
                     "and Business Infrastructure teams every year."]


def test_chrome_fragment_is_dropped_rather_than_half_cleaned():
    """Stripping chrome off a mid-clause fragment leaves a fragment, not evidence.

    "Expand Expand and increase our efforts..." is UI text glued to a bullet.
    Emitting "and increase our efforts..." into an evidence block would look
    broken to a sponsor, so it is dropped entirely.
    """
    text = ("Expand Expand and increase our efforts towards new opportunities in "
            "energy markets and retrofitting older buildings.")
    assert extract.sentences(text) == []


def test_deal_partnership_is_not_a_student_partnership():
    """'partnership with' matches investment deals far more often than campuses."""
    deal = ("In 2025, the firm invested in Sorgenia in partnership with F2i SGR to "
            "create a diversified energy infrastructure platform in Europe.")
    fields = extract.extract_fields(deal, "https://x.com", "news")
    assert fields["existing_student_org_partnerships"] == []


def test_real_student_partnership_is_captured():
    real = ("We work in partnership with Management Leadership for Tomorrow to bring "
            "undergraduate students into our summer analyst program each year.")
    fields = extract.extract_fields(real, "https://x.com", "university_recruiting")
    assert fields["existing_student_org_partnerships"]
    assert fields["existing_student_org_partnerships"][0]["source_url"] == "https://x.com"


def test_careers_scoring_rejects_investment_pages():
    """'opportunities' pulled investment-strategy pages into the careers slot."""
    careers = extract.CATEGORY_LINK_KEYWORDS["careers"]
    negatives = extract.CATEGORY_NEGATIVE_KEYWORDS["careers"]
    real = extract.score_link("https://x.com/careers/", "Careers", careers, negatives)
    deal = extract.score_link(
        "https://x.com/investment-strategy/global-opportunities/",
        "Global Opportunities", careers, negatives,
    )
    assert real > deal


def test_every_extracted_item_carries_a_source_url():
    text = ("We hire interns for our summer analyst program. Our values are "
            "collaboration and curiosity across every team we build.")
    for items in extract.extract_fields(text, "https://x.com/careers", "careers").values():
        for item in items:
            assert item["source_url"].startswith("https://")
            assert item["quote"]
