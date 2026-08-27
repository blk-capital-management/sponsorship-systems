"""Acceptance tests 1 and 3.

1. A draft containing a claim with no matching source_url fails generation.
3. Any em dash in generated output fails.
"""

import json
from pathlib import Path

import pytest

from common.provenance import (
    ProvenanceError,
    assert_no_em_dash,
    build_evidence_block,
    check_firm_paragraph,
    find_tone_violations,
    split_sentences,
)
from research.hook_ids import artifact_with_hook_ids, resolve_selected_hooks


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CITADEL_PARAGRAPH = (
    "Citadel’s focus on developing exceptional undergraduates across the U.S., "
    "Europe, and APAC makes its early-career programs especially relevant to "
    "BLK’s student network. I was also drawn to the firm’s emphasis on giving "
    "people the opportunity to contribute their best thinking regardless of "
    "title or tenure, which makes a partnership with BLK a natural fit for "
    "connecting Citadel with ambitious undergraduate talent."
)


def _general_atlantic_hooks(*text_fragments: str):
    """Resolve only the requested stored General Atlantic hooks by stable ID."""
    artifact = artifact_with_hook_ids(json.loads(
        (PROJECT_ROOT / "research" / "out" / "general_atlantic.json").read_text(
            encoding="utf-8"
        )
    ))
    selected_ids = [
        hook["research_hook_id"]
        for hook in artifact["alignment_hooks"]
        if any(fragment in hook.get("text", "") for fragment in text_fragments)
    ]
    assert len(selected_ids) == len(text_fragments)
    hooks, resolved_ids = resolve_selected_hooks(artifact, selected_ids)
    assert resolved_ids == selected_ids
    return hooks


# ── Acceptance test 1: no source_url means no claim ───────────────────────────

def test_supported_paragraph_passes(hooks, blk_facts):
    paragraph = (
        "Sixth Street's Early Careers programs already bring undergraduate students "
        "into contact with your professionals. BLK members are selected through a "
        "multi-round process, so that pipeline arrives pre-vetted."
    )
    report = check_firm_paragraph(paragraph, hooks, blk_facts)
    assert report.ok, report.describe()
    assert any(s.source_url for s in report.sentences)


def test_invented_firm_fact_fails(hooks, blk_facts):
    """A claim about a program that appears in no hook must fail generation."""
    paragraph = (
        "Sixth Street's Early Careers programs bring undergraduate students in. "
        "Your Chicago office also runs a Sophomore Springboard program each January."
    )
    report = check_firm_paragraph(paragraph, hooks, blk_facts)
    assert not report.ok
    joined = " ".join(report.violations)
    assert "no source_url backs" in joined
    assert "Springboard" in joined or "Chicago" in joined


def test_invented_number_fails(hooks, blk_facts):
    paragraph = (
        "Sixth Street's Early Careers programs reach undergraduate students. "
        "Your team hired 47 interns last summer across four offices."
    )
    report = check_firm_paragraph(paragraph, hooks, blk_facts)
    assert not report.ok
    assert any("47" in v for v in report.violations)


def test_hooks_without_source_url_block_everything(blk_facts):
    """A hook missing its source_url is unusable, not merely weaker."""
    unsourced = [{"text": "They run a campus program.", "source_url": "", "quote": "x"}]
    report = check_firm_paragraph("They run a campus program. It is large.",
                                  unsourced, blk_facts)
    assert not report.ok
    assert any("no alignment_hooks carry a source_url" in v for v in report.violations)


def test_blk_facts_vocabulary_is_allowed(hooks, blk_facts):
    """BLK's own approved numbers are not firm claims and must not be flagged."""
    paragraph = (
        "Sixth Street's Early Careers programs reach undergraduate students. "
        "BLK draws members from 200+ universities across the US and EMEA."
    )
    report = check_firm_paragraph(paragraph, hooks, blk_facts)
    assert report.ok, report.describe()


# ── Acceptance test 3: em dashes ──────────────────────────────────────────────

@pytest.mark.parametrize("dash", ["—", "–", "―"])
def test_em_dash_variants_raise(dash):
    with pytest.raises(ProvenanceError, match="Em dash"):
        assert_no_em_dash(f"BLK members are vetted {dash} yours are not.")


def test_ascii_em_dash_substitute_raises():
    with pytest.raises(ProvenanceError, match="ASCII em dash"):
        assert_no_em_dash("BLK members are vetted -- yours are not.")


def test_hyphen_in_date_range_is_fine():
    assert_no_em_dash("Our 2026-27 prospectus is attached.")


def test_em_dash_in_paragraph_fails_generation(hooks, blk_facts):
    paragraph = (
        "Sixth Street's Early Careers programs reach undergraduate students — "
        "that is the same group BLK selects from. BLK members arrive pre-vetted."
    )
    report = check_firm_paragraph(paragraph, hooks, blk_facts)
    assert not report.ok
    assert any("Em dash" in v for v in report.violations)


# ── House tone ────────────────────────────────────────────────────────────────

def test_flattery_is_rejected(hooks, blk_facts):
    paragraph = (
        "Sixth Street's prestigious Early Careers programs reach undergraduate "
        "students. We are thrilled to connect with your team."
    )
    report = check_firm_paragraph(paragraph, hooks, blk_facts)
    assert not report.ok
    assert find_tone_violations(paragraph) == ["prestigious", "thrilled"]


def test_sentence_count_bounds(hooks, blk_facts):
    one = "Sixth Street's Early Careers programs reach undergraduate students."
    report = check_firm_paragraph(one, hooks, blk_facts)
    assert any("sentence(s)" in v for v in report.violations)


# ── Evidence block ────────────────────────────────────────────────────────────

def test_evidence_block_lists_a_source_per_claim(hooks, blk_facts):
    paragraph = (
        "Sixth Street's Early Careers programs reach undergraduate students. "
        "People at Sixth Street are intellectually curious by nature."
    )
    report = check_firm_paragraph(paragraph, hooks, blk_facts)
    block = build_evidence_block(report, hooks)
    assert "https://sixthstreet.com/careers/" in block
    assert "https://sixthstreet.com/values-and-culture/" in block


def test_split_sentences_handles_short_paragraphs():
    assert len(split_sentences("One thing here. Two things there. Three now.")) == 3


# ── Citadel selected-hook regression ─────────────────────────────────────────

def test_citadel_paragraph_passes_with_sentence_level_selected_hook_provenance(
    blk_facts,
):
    artifact = artifact_with_hook_ids(json.loads(
        (PROJECT_ROOT / "research" / "out" / "citadel.json").read_text(encoding="utf-8")
    ))
    report = check_firm_paragraph(
        CITADEL_PARAGRAPH,
        artifact["alignment_hooks"],
        blk_facts,
        firm_name="Citadel",
    )

    assert report.ok, report.describe()
    assert len(report.sentences) == 2
    assert all(sentence.source_urls for sentence in report.sentences)
    assert all(sentence.hook_ids for sentence in report.sentences)
    assert any("discover-citadel" in url for url in report.sentences[0].source_urls)
    assert any("our-culture" in url for url in report.sentences[1].source_urls)


@pytest.mark.parametrize("paragraph", [
    "Citadel hires more BLK members than any competing hedge fund. BLK would welcome a conversation.",
    "Citadel plans to double its undergraduate internship class next year. BLK would welcome a conversation.",
    "Citadel has specifically identified BLK Capital Management as a recruiting priority. BLK would welcome a conversation.",
])
def test_citadel_unsupported_claims_fail_with_actionable_sentence_message(
    paragraph, blk_facts,
):
    artifact = artifact_with_hook_ids(json.loads(
        (PROJECT_ROOT / "research" / "out" / "citadel.json").read_text(encoding="utf-8")
    ))
    report = check_firm_paragraph(
        paragraph,
        artifact["alignment_hooks"],
        blk_facts,
        firm_name="Citadel",
    )

    assert not report.ok
    message = report.describe()
    assert "Sentence 1" in message
    assert "selected research hooks" in message
    assert "Unsupported phrase" in message


# ── General Atlantic matched-span regression ─────────────────────────────────

def test_supported_category_leading_span_passes_without_treating_rule_as_phrase(
    blk_facts,
):
    hooks = _general_atlantic_hooks("category-leading companies")
    paragraph = (
        "General Atlantic’s focus on supporting category-leading companies through "
        "strategic counsel, resources and value-add capabilities creates a strong fit "
        "with BLK’s pre-vetted undergraduate network."
    )

    report = check_firm_paragraph(
        paragraph,
        hooks,
        blk_facts,
        firm_name="General Atlantic",
        min_sentences=1,
    )

    assert report.ok, report.describe()
    assert report.sentences[0].unsupported_claims == []
    assert report.sentences[0].hook_ids == [hooks[0]["research_hook_id"]]


def test_supported_actis_fact_and_blk_interpretation_pass(blk_facts):
    hooks = _general_atlantic_hooks("critical infrastructure")
    paragraph = (
        "General Atlantic’s investment in critical infrastructure through Actis "
        "creates a relevant avenue for BLK students interested in these areas."
    )

    report = check_firm_paragraph(
        paragraph,
        hooks,
        blk_facts,
        firm_name="General Atlantic",
        min_sentences=1,
    )

    assert report.ok, report.describe()
    assert report.sentences[0].unsupported_claims == []
    assert report.sentences[0].hook_ids == [hooks[0]["research_hook_id"]]


def test_current_general_atlantic_two_hook_paragraph_passes(blk_facts):
    hooks = _general_atlantic_hooks(
        "category-leading companies",
        "critical infrastructure",
    )
    paragraph = (
        "General Atlantic’s focus on supporting category-leading companies through "
        "strategic counsel, resources and value-add capabilities, alongside its "
        "investment in critical infrastructure through Actis, creates a strong fit "
        "with BLK’s pre-vetted undergraduate network."
    )

    report = check_firm_paragraph(
        paragraph,
        hooks,
        blk_facts,
        firm_name="General Atlantic",
        min_sentences=1,
    )

    assert report.ok, report.describe()
    assert set(report.sentences[0].hook_ids) == {
        hook["research_hook_id"] for hook in hooks
    }


def test_unsupported_healthcare_claim_reports_actual_spans(blk_facts):
    hooks = _general_atlantic_hooks("category-leading companies")
    paragraph = "General Atlantic is one of the largest healthcare investors in the world."

    report = check_firm_paragraph(
        paragraph,
        hooks,
        blk_facts,
        firm_name="General Atlantic",
        min_sentences=1,
    )

    assert not report.ok
    claims = report.sentences[0].unsupported_claims
    assert any(
        claim.phrase == "largest" and claim.claim_type == "superlative"
        for claim in claims
    )
    assert any(claim.phrase.lower().startswith("healthcare") for claim in claims)
    assert "superlative" not in report.sentences[0].unsupported_terms
    assert "largest" in report.describe()


def test_unsupported_largest_reports_phrase_and_separate_rule_type(blk_facts):
    hooks = _general_atlantic_hooks("category-leading companies")
    paragraph = "General Atlantic is the largest growth equity investor."

    report = check_firm_paragraph(
        paragraph,
        hooks,
        blk_facts,
        firm_name="General Atlantic",
        min_sentences=1,
    )

    assert not report.ok
    claims = report.sentences[0].unsupported_claims
    assert any(
        claim.phrase == "largest" and claim.claim_type == "superlative"
        for claim in claims
    )
    assert "superlative" not in report.sentences[0].unsupported_terms
    assert "Unsupported phrase: 'largest" in report.describe()
