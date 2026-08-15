"""Part D tests for drafts/generate.py: the locked cold-prospect template,
the C5 email_body validators, zero-hook routing, and the house subject line.
"""

import json

import pytest

from common.namespaces import build_contact_provenance
from common.provenance import assert_no_em_dash
from contacts.record import ContactRecord
from drafts import generate
from drafts.routing import COLD_PROSPECT

LOCKED_TEMPLATE_TEXT = """Hi {contact_first_name},

My name is Jamari Myers, and I am the Co-Chair of Sponsorships at BLK Capital Management, a student-run finance nonprofit now in its tenth year.

BLK exists to give firms direct access to a pre-vetted pipeline of undergraduate finance talent. Members are accepted through a multi-round selection process, and we received {applications_last_cycle} applications this past cycle. What that process produces is a network of {member_count} members across {university_count} universities in the US and EMEA who consistently place into investment banking, private equity, private credit, and public markets roles. More on our members, outcomes, and current partners is at blkcapitalmanagement.org.

{firm_specific_paragraph}

Partner firms engage with our members across the year through virtual programming, resume access, and in-person touchpoints, anchored by our Fall Conference on {conference_dates} in New York, hosted at {conference_venue} this year. What a partnership provides is access rather than obligation. Your team meets prepared candidates early, and those candidates go through your process on their own merits. If it would be useful, I am happy to follow up with our full sponsorship prospectus ahead of a call.

We would welcome the chance to talk with {firm_name} about beginning a partnership that grows over time. If this is of interest, please connect me with the right people on your team and we will get a call scheduled.

Thank you for your time and consideration.

Best,
Jamari Myers
Co-Chair of Sponsorships, BLK Capital Management
"""


@pytest.fixture
def facts() -> dict:
    return {
        "org_name": "BLK Capital Management",
        "year_descriptor": "now in its tenth year",
        "applications_last_cycle": "1,750+",
        "member_count": "1500+",
        "universities": "200+",
        "regions": "US and EMEA",
        "emea_members": 141,
        "fall_conference": {
            "dates": "November 12 and 13",
            "city": "New York",
            "host": "Wells Fargo",
        },
        "website": "blkcapitalmanagement.org",
        "placement_verticals": [
            "investment banking", "private equity", "private credit",
            "asset management",
        ],
    }


@pytest.fixture
def hooks() -> list[dict]:
    return [
        {
            "text": (
                "We hire interns for roles on our Investment, Technology, and "
                "Business Infrastructure Teams, seeking to convert top "
                "performers to full-time employees."
            ),
            "firm_claim_source": "https://www.bamfunds.com/careers/internships",
            "quote": (
                "We hire interns for roles on our Investment, Technology, and "
                "Business Infrastructure Teams, seeking to convert top "
                "performers to full-time employees."
            ),
            "basis": "campus_or_early_careers_programs",
        },
        {
            "text": "At Balyasny, we believe great ideas emerge from the connections between people.",
            "firm_claim_source": "https://www.bamfunds.com/careers",
            "quote": "At Balyasny, we believe great ideas emerge from the connections between people.",
            "basis": "values_themes",
        },
    ]


@pytest.fixture
def artifact(hooks) -> dict:
    return {
        "firm": "Balyasny Asset Management",
        "firm_slug": "balyasny",
        "domain": "bamfunds.com",
        "region": "US",
        "relationship": "prospect",
        "fetched_at": "2026-08-13T00:00:00+00:00",
        "pages_crawled": [],
        "alignment_hooks": hooks,
        "firm_claim_sources": [h["firm_claim_source"] for h in hooks],
        "confidence": "high",
        "gaps": [],
    }


@pytest.fixture
def contact() -> ContactRecord:
    return ContactRecord(
        name="Hannah Dinardo",
        owner="jamari",
        title="Head of Campus Recruiting",
        email="hdinardo@bamfunds.com",
        contact_provenance=build_contact_provenance(
            "https://www.google.com/search?q=site:linkedin.com hannah dinardo bamfunds",
            "https://www.google.com/search?q=site:linkedin.com hannah dinardo bamfunds",
            method="pattern_inference",
            provider="hunter",
        ),
    )


GOOD_PARAGRAPH = (
    "Balyasny runs its internship program across specific teams, Investment, "
    "Technology, and Business Infrastructure, with a clear track from intern "
    "to full-time offer for top performers. BLK selects its own members "
    "through a multi-round process built to produce undergraduates who can "
    "compete for a seat on one of those teams from day one."
)


# ── The locked template ────────────────────────────────────────────────────────

def test_template_matches_the_locked_text_byte_for_byte():
    on_disk = generate.TEMPLATE_PATH.read_text(encoding="utf-8")
    assert on_disk == LOCKED_TEMPLATE_TEXT, (
        "templates/cold_prospect.md has drifted from the text Jamari locked in "
        "C1. Every future draft renders from this file; it is not editable "
        "in place."
    )


# ── Fixed blk_facts-sourced fields ────────────────────────────────────────────

def test_fixed_fields_render_exactly_from_blk_facts(facts):
    fields = generate.fixed_fields_from_facts(facts)
    assert fields == {
        "applications_last_cycle": "1,750+",
        "member_count": "1500+",
        "university_count": "200+",
        "conference_dates": "November 12 and 13",
        "conference_venue": "Wells Fargo",
    }


@pytest.mark.parametrize("field_name", [
    "applications_last_cycle", "member_count", "university_count",
    "conference_dates", "conference_venue",
])
def test_a_fixed_field_value_not_in_blk_facts_is_blocked(facts, field_name):
    fields = generate.fixed_fields_from_facts(facts)
    fields[field_name] = "9,999+"
    with pytest.raises(generate.DraftGenerationError, match=field_name):
        generate.assert_fixed_fields_match_facts(fields, facts)


def test_fixed_fields_matching_facts_pass(facts):
    fields = generate.fixed_fields_from_facts(facts)
    generate.assert_fixed_fields_match_facts(fields, facts)  # does not raise


# ── Full draft generation ──────────────────────────────────────────────────────

def _generate(artifact, contact, facts, tmp_path, paragraph=GOOD_PARAGRAPH):
    return generate.generate_draft(
        artifact, contact, facts,
        template_text=LOCKED_TEMPLATE_TEXT,
        firm_specific_paragraph=paragraph,
        out_dir=tmp_path / "drafts",
        review_dir=tmp_path / "review",
    )


def test_a_well_formed_draft_generates_and_writes_both_files(artifact, contact, facts, tmp_path):
    record = _generate(artifact, contact, facts, tmp_path)

    assert (tmp_path / "drafts" / "balyasny.json").exists()
    assert (tmp_path / "drafts" / "balyasny.txt").exists()
    assert "{firm_specific_paragraph}" not in record["email_body"]
    assert "Hannah" in record["email_body"]
    assert "Balyasny Asset Management" in record["email_body"]


def test_contact_provenance_url_never_appears_in_the_rendered_body(artifact, contact, facts, tmp_path):
    record = _generate(artifact, contact, facts, tmp_path)
    for url in generate.contact_provenance_urls(contact.contact_provenance):
        assert url not in record["email_body"]
    # It does surface in the internal evidence block, which never becomes email_body.
    assert record["evidence_block"] != record["email_body"]


def test_subject_comes_from_the_house_template_and_is_flagged_as_such(
    artifact, contact, facts, tmp_path
):
    record = _generate(artifact, contact, facts, tmp_path)
    assert record["subject"] == generate.SUBJECT_BY_STATUS[COLD_PROSPECT]
    assert record["subject_status"] == "template_default"

    on_disk = json.loads((tmp_path / "drafts" / "balyasny.json").read_text(encoding="utf-8"))
    assert on_disk["subject"] == record["subject"]
    assert on_disk["subject_status"] == "template_default"


def test_no_subject_line_carries_a_firm_specific_claim_or_an_em_dash(artifact, contact, facts, tmp_path):
    """Subjects are fixed house lines. A firm name in one would be an unsourced claim."""
    record = _generate(artifact, contact, facts, tmp_path)
    assert "Balyasny" not in record["subject"]
    for subject in generate.SUBJECT_BY_STATUS.values():
        assert_no_em_dash(subject)
        assert "{" not in subject and "}" not in subject


# ── C5 validators ──────────────────────────────────────────────────────────────

def test_an_attachment_claim_phrase_is_blocked(artifact, contact, facts):
    fields = {
        "contact_first_name": "Hannah",
        "firm_name": artifact["firm"],
        "firm_specific_paragraph": GOOD_PARAGRAPH,
        **generate.fixed_fields_from_facts(facts),
    }
    body = generate.render_email_body(LOCKED_TEMPLATE_TEXT, fields)
    body += "\n\nPlease find attached our 2026-27 prospectus."

    with pytest.raises(generate.DraftGenerationError, match="PDF-attachment"):
        generate.validate_email_body(
            body, fields=fields, facts=facts, artifact=artifact,
            contact=contact, usable_hooks=artifact["alignment_hooks"],
        )


def test_a_verbatim_8plus_word_run_from_a_hook_is_blocked(hooks):
    copied_paragraph = (
        "We hire interns for roles on our Investment, Technology, and "
        "Business Infrastructure Teams, seeking to convert top performers "
        "to full-time employees. BLK selects members through a multi-round "
        "process."
    )
    run = generate.find_verbatim_hook_run(copied_paragraph, hooks)
    assert run is not None
    assert len(run.split()) >= generate.MIN_VERBATIM_RUN


def test_a_verbatim_hook_run_blocks_the_whole_draft(artifact, contact, facts):
    copied_paragraph = (
        "We hire interns for roles on our Investment, Technology, and "
        "Business Infrastructure Teams, seeking to convert top performers "
        "to full-time employees. BLK selects members through its own process."
    )
    fields = {
        "contact_first_name": "Hannah",
        "firm_name": artifact["firm"],
        "firm_specific_paragraph": copied_paragraph,
        **generate.fixed_fields_from_facts(facts),
    }
    body = generate.render_email_body(LOCKED_TEMPLATE_TEXT, fields)

    with pytest.raises(generate.DraftGenerationError, match="copies"):
        generate.validate_email_body(
            body, fields=fields, facts=facts, artifact=artifact,
            contact=contact, usable_hooks=artifact["alignment_hooks"],
        )


def test_a_firm_claim_source_url_leaking_into_the_body_is_blocked(artifact, contact, facts):
    fields = {
        "contact_first_name": "Hannah",
        "firm_name": artifact["firm"],
        "firm_specific_paragraph": GOOD_PARAGRAPH,
        **generate.fixed_fields_from_facts(facts),
    }
    body = generate.render_email_body(LOCKED_TEMPLATE_TEXT, fields)
    body += f"\n\nSee {artifact['firm_claim_sources'][0]} for more."

    with pytest.raises(generate.DraftGenerationError, match="leaked"):
        generate.validate_email_body(
            body, fields=fields, facts=facts, artifact=artifact,
            contact=contact, usable_hooks=artifact["alignment_hooks"],
        )


# ── Zero-hook routing ──────────────────────────────────────────────────────────

def test_zero_hook_artifact_produces_no_draft(contact, facts, tmp_path):
    zero_hook_artifact = {
        "firm": "No Hooks Capital", "firm_slug": "no_hooks", "domain": "nohooks.com",
        "region": "US", "relationship": "prospect", "fetched_at": "2026-08-13T00:00:00+00:00",
        "pages_crawled": [], "alignment_hooks": [], "firm_claim_sources": [],
        "confidence": "high", "gaps": ["no citable content found for values_themes"],
    }
    out_dir = tmp_path / "drafts"
    review_dir = tmp_path / "review"

    with pytest.raises(generate.NoUsableHooksError):
        generate.generate_draft(
            zero_hook_artifact, contact, facts,
            template_text=LOCKED_TEMPLATE_TEXT,
            out_dir=out_dir, review_dir=review_dir,
        )

    assert not out_dir.exists() or not list(out_dir.iterdir())

    import csv
    rows = list(csv.DictReader((review_dir / "manual_queue.csv").open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["firm"] == "No Hooks Capital"
    assert "firm_claim_source" in rows[0]["reason"]


def test_hooks_with_no_firm_claim_source_count_as_zero_hooks(contact, facts, tmp_path):
    """An alignment_hook present but unsourced is not usable (rule 1)."""
    artifact = {
        "firm": "Half Sourced LLC", "firm_slug": "half_sourced", "domain": "half.com",
        "region": "US", "relationship": "prospect", "fetched_at": "2026-08-13T00:00:00+00:00",
        "pages_crawled": [],
        "alignment_hooks": [{"text": "We run a campus program.", "firm_claim_source": "",
                             "quote": "We run a campus program.", "basis": "values_themes"}],
        "firm_claim_sources": [], "confidence": "medium", "gaps": [],
    }
    with pytest.raises(generate.NoUsableHooksError):
        generate.generate_draft(
            artifact, contact, facts,
            template_text=LOCKED_TEMPLATE_TEXT,
            out_dir=tmp_path / "drafts", review_dir=tmp_path / "review",
        )
