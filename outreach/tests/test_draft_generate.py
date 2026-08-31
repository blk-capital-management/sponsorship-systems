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
from research import hook_ids
from research.hook_ids import artifact_with_hook_ids

LOCKED_TEMPLATE_TEXT = """Hi {contact_first_name},

My name is Jamari Myers, and I am the Co-Chair of Sponsorships at BLK Capital Management, a student-run finance nonprofit now in its tenth year.

As we finalize our fall partner programming ahead of our {conference_dates} Fall Conference in {conference_city}, I wanted to reach out about establishing a relationship with {firm_name}.

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
        "conference_city": "New York",
        "conference_venue": "Wells Fargo",
    }


@pytest.mark.parametrize("field_name", [
    "applications_last_cycle", "member_count", "university_count",
    "conference_dates", "conference_city", "conference_venue",
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


def test_selected_citadel_hooks_are_resolved_and_persisted_internal_only(
    contact, facts, tmp_path,
):
    citadel = artifact_with_hook_ids(json.loads(
        (generate.PROJECT_ROOT / "research" / "out" / "citadel.json").read_text(
            encoding="utf-8"
        )
    ))
    selected = [hook["research_hook_id"] for hook in citadel["alignment_hooks"][1:]]
    paragraph = (
        "Citadel’s focus on developing exceptional undergraduates across the U.S., "
        "Europe, and APAC makes its early-career programs especially relevant to "
        "BLK’s student network. I was also drawn to the firm’s emphasis on giving "
        "people the opportunity to contribute their best thinking regardless of "
        "title or tenure, which makes a partnership with BLK a natural fit for "
        "connecting Citadel with ambitious undergraduate talent."
    )
    record = generate.generate_draft(
        citadel,
        contact,
        facts,
        target={"firm": "Citadel", "owner": "jamari", "contact_status": "cold_prospect"},
        template_text=LOCKED_TEMPLATE_TEXT,
        firm_specific_paragraph=paragraph,
        supporting_hook_ids=selected,
        out_dir=tmp_path / "drafts",
        review_dir=tmp_path / "review",
    )

    provenance = record["fields"]["firm_paragraph_provenance"]
    assert provenance["internal_only"] is True
    assert provenance["supporting_hook_ids"] == selected
    assert len(provenance["sentence_mappings"]) == 2
    assert all(mapping["hook_ids"] for mapping in provenance["sentence_mappings"])
    assert "research_hook_id" not in record["email_body"]
    assert not any(hook_id in record["email_body"] for hook_id in selected)


def test_unknown_client_hook_id_cannot_supply_evidence(artifact, contact, facts, tmp_path):
    with pytest.raises(generate.DraftGenerationError, match="stored artifact"):
        generate.generate_draft(
            artifact,
            contact,
            facts,
            template_text=LOCKED_TEMPLATE_TEXT,
            firm_specific_paragraph=GOOD_PARAGRAPH,
            supporting_hook_ids=["rhook_client_supplied_url_is_not_evidence"],
            out_dir=tmp_path / "drafts",
            review_dir=tmp_path / "review",
        )


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


# ── Zero-hook drafting ─────────────────────────────────────────────────────────
#
# Research returning nothing is a normal outcome for a firm with a thin public
# footprint, not an error. These used to assert a refusal; they now assert the
# draft is produced and the absence is recorded for the reviewer.

ZERO_HOOK_PARAGRAPH = (
    "Balyasny staffs its investment teams around portfolio managers who run "
    "their own books. BLK selects its members through a multi-round process "
    "built to produce undergraduates who can contribute on such a team early."
)


def _provenance(record):
    return record["fields"]["firm_paragraph_provenance"]


def test_zero_hook_artifact_still_produces_a_draft(contact, facts, tmp_path):
    zero_hook_artifact = {
        "firm": "No Hooks Capital", "firm_slug": "no_hooks", "domain": "nohooks.com",
        "region": "US", "relationship": "prospect", "fetched_at": "2026-08-13T00:00:00+00:00",
        "pages_crawled": [], "alignment_hooks": [], "firm_claim_sources": [],
        "confidence": "high", "gaps": ["no citable content found for values_themes"],
    }
    out_dir = tmp_path / "drafts"
    review_dir = tmp_path / "review"

    record = generate.generate_draft(
        zero_hook_artifact, contact, facts,
        template_text=LOCKED_TEMPLATE_TEXT,
        firm_specific_paragraph=ZERO_HOOK_PARAGRAPH,
        supporting_hook_ids=[],
        out_dir=out_dir, review_dir=review_dir,
    )

    assert (out_dir / "no_hooks.json").exists()
    provenance = _provenance(record)
    assert provenance["grounding_status"] == "no_research_available"
    assert provenance["hook_count"] == 0
    assert provenance["hooks_used"] == []
    assert record["validator_results"]["status"] == "pass"
    assert record["validator_results"]["grounding_status"] == "no_research_available"

    # Missing research is informational. Nothing is routed to the manual queue
    # from the draft path any more.
    assert not (review_dir / "manual_queue.csv").exists()


def test_hooks_with_no_firm_claim_source_count_as_zero_hooks(contact, facts, tmp_path):
    """An alignment_hook present but unsourced is still not usable evidence."""
    artifact = {
        "firm": "Half Sourced LLC", "firm_slug": "half_sourced", "domain": "half.com",
        "region": "US", "relationship": "prospect", "fetched_at": "2026-08-13T00:00:00+00:00",
        "pages_crawled": [],
        "alignment_hooks": [{"text": "We run a campus program.", "firm_claim_source": "",
                             "quote": "We run a campus program.", "basis": "values_themes"}],
        "firm_claim_sources": [], "confidence": "medium", "gaps": [],
    }
    record = generate.generate_draft(
        artifact, contact, facts,
        template_text=LOCKED_TEMPLATE_TEXT,
        firm_specific_paragraph=ZERO_HOOK_PARAGRAPH,
        supporting_hook_ids=[],
        out_dir=tmp_path / "drafts", review_dir=tmp_path / "review",
    )
    provenance = _provenance(record)
    assert provenance["hook_count"] == 0
    assert provenance["grounding_status"] == "no_research_available"


def test_zero_hook_draft_is_a_review_item_and_sends_nothing(contact, facts, tmp_path):
    """The relaxed path is still review-only. Rule 2 is untouched."""
    artifact = {
        "firm": "No Hooks Capital", "firm_slug": "no_hooks", "domain": "nohooks.com",
        "region": "US", "relationship": "prospect", "fetched_at": "2026-08-13T00:00:00+00:00",
        "pages_crawled": [], "alignment_hooks": [], "firm_claim_sources": [],
        "confidence": "high", "gaps": [],
    }
    record = generate.generate_draft(
        artifact, contact, facts,
        template_text=LOCKED_TEMPLATE_TEXT,
        firm_specific_paragraph=ZERO_HOOK_PARAGRAPH,
        supporting_hook_ids=[],
        out_dir=tmp_path / "drafts", review_dir=tmp_path / "review",
    )
    assert "sent_at" not in record
    assert set(record) >= {"email_body", "evidence_block", "validator_results"}
    assert not list((tmp_path / "drafts").glob("*.sent"))


# ── Grounding is recorded, never enforced ─────────────────────────────────────

def test_paragraph_that_does_not_match_a_hook_still_generates(artifact, contact, facts, tmp_path):
    """The old paragraph-to-hook gate. A human wrote this; the validator reports."""
    ungrounded = (
        "Balyasny has built out its systematic research effort in Singapore over "
        "the last two years. BLK selects its members through a multi-round "
        "process designed to produce undergraduates ready for that kind of desk."
    )
    record = _generate(artifact, contact, facts, tmp_path, paragraph=ungrounded)
    provenance = _provenance(record)
    assert provenance["grounding_status"] == "ungrounded"
    assert provenance["hook_count"] > 0
    assert provenance["hooks_used"], "hooks were available and selected"
    # The signal is kept, just not enforced.
    assert provenance["advisories"]


def test_a_matching_paragraph_is_recorded_as_grounded(artifact, contact, facts, tmp_path):
    record = _generate(artifact, contact, facts, tmp_path)
    assert _provenance(record)["grounding_status"] == "grounded"


def test_one_sentence_paragraph_is_allowed(artifact, contact, facts, tmp_path):
    """Sentence count is a house guideline now, not a gate."""
    record = _generate(
        artifact, contact, facts, tmp_path,
        paragraph="Balyasny runs its internship program across specific teams.",
    )
    assert record["fields"]["firm_specific_paragraph"].startswith("Balyasny")


# ── What still blocks ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("paragraph", [None, "", "   \n  "])
def test_an_empty_paragraph_still_blocks(artifact, contact, facts, tmp_path, paragraph):
    with pytest.raises(generate.DraftGenerationError, match="human-authored"):
        _generate(artifact, contact, facts, tmp_path, paragraph=paragraph)


def test_a_missing_contact_still_blocks(artifact, facts, tmp_path):
    with pytest.raises(generate.DraftGenerationError, match="contact record"):
        _generate(artifact, None, facts, tmp_path)


def test_an_unresolved_merge_field_still_blocks_a_zero_hook_draft(contact, facts, tmp_path):
    """Loosening research validation must not leak into stat validation."""
    artifact = {
        "firm": "No Hooks Capital", "firm_slug": "no_hooks", "domain": "nohooks.com",
        "region": "US", "relationship": "prospect", "fetched_at": "2026-08-13T00:00:00+00:00",
        "pages_crawled": [], "alignment_hooks": [], "firm_claim_sources": [],
        "confidence": "high", "gaps": [],
    }
    out_dir = tmp_path / "drafts"
    missing = {key: value for key, value in facts.items() if key != "member_count"}
    with pytest.raises(KeyError, match="member_count"):
        generate.generate_draft(
            artifact, contact, missing,
            template_text=LOCKED_TEMPLATE_TEXT,
            firm_specific_paragraph=ZERO_HOOK_PARAGRAPH,
            supporting_hook_ids=[],
            out_dir=out_dir, review_dir=tmp_path / "review",
        )
    assert not out_dir.exists() or not list(out_dir.iterdir())


def test_a_drifted_stat_still_blocks_a_zero_hook_draft(contact, facts, tmp_path):
    """Merge-field resolution stays strict on the relaxed path."""
    artifact = {
        "firm": "No Hooks Capital", "firm_slug": "no_hooks", "domain": "nohooks.com",
        "region": "US", "relationship": "prospect", "fetched_at": "2026-08-13T00:00:00+00:00",
        "pages_crawled": [], "alignment_hooks": [], "firm_claim_sources": [],
        "confidence": "high", "gaps": [],
    }
    record = generate.generate_draft(
        artifact, contact, facts,
        template_text=LOCKED_TEMPLATE_TEXT,
        firm_specific_paragraph=ZERO_HOOK_PARAGRAPH,
        supporting_hook_ids=[],
        out_dir=tmp_path / "drafts", review_dir=tmp_path / "review",
    )
    drifted = {**record["fields"], "member_count": "9,999+"}
    with pytest.raises(generate.DraftGenerationError, match="member_count"):
        generate.assert_fixed_fields_match_facts(drifted, facts)


def test_an_empty_hook_selection_resolves_to_no_hooks(artifact):
    decorated = artifact_with_hook_ids(artifact)
    hooks, ids = hook_ids.resolve_selected_hooks(decorated, [])
    assert (hooks, ids) == ([], [])


def test_an_unknown_hook_id_still_raises(artifact):
    """Artifact integrity is not a grounding check and stays strict."""
    decorated = artifact_with_hook_ids(artifact)
    with pytest.raises(hook_ids.ResearchHookSelectionError):
        hook_ids.resolve_selected_hooks(decorated, ["rhook_notreal"])
