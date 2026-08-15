"""Phase 4b warm-renewal and recovery drafting acceptance tests."""

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from common.config import load_blk_facts
from common.namespaces import build_contact_provenance
from common.provenance import assert_no_em_dash
from contacts.record import ContactRecord
from drafts import generate
from drafts.routing import (
    EXISTING_PARTNER,
    LAPSED_PARTNER,
    NEEDS_WARM_CSV,
    DraftRoutingError,
    route_target,
)
from scripts.derive_target_status import CrmRow, Derivation, safe_crm_text, write_targets

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _warm_target(**overrides):
    target = {
        "firm": "Sixth Street",
        "domain": "sixthstreet.com",
        "owner": "jamari",
        "contact_status": EXISTING_PARTNER,
        "has_known_contact": "TRUE",
        "relationship_record_id": "S048",
        "relationship_tier": "Gold",
        "relationship_status": "Active",
        "relationship_contact_name": "Maddy, Skylar, Madeleine",
        "relationship_contact_email": (
            "mlivingston@sixthstreet.com; sburdick@sixthstreet.com; "
            "mparker@sixthstreet.com"
        ),
        "relationship_expiration": "2026-09-08",
        "relationship_decline_reason": "",
        "relationship_crm_source": "Jamari!r21",
    }
    target.update(overrides)
    return target


def _recovery_target(**overrides):
    target = {
        "firm": "Point72 Asset Management",
        "domain": "point72.com",
        "owner": "jamari",
        "contact_status": LAPSED_PARTNER,
        "has_known_contact": "TRUE",
        "relationship_record_id": "",
        "relationship_tier": "Diamond",
        "relationship_status": "Not Renewing",
        "relationship_contact_name": "Kelli & Holly",
        "relationship_contact_email": "Kelli.Dougherty@point72.com",
        "relationship_expiration": "2025-12-31",
        "relationship_decline_reason": "Budget cuts; cannot re-sponsor.",
        "relationship_crm_source": "Archive!r11",
    }
    target.update(overrides)
    return target


def test_complete_warm_and_recovery_targets_route_to_their_templates(tmp_path):
    assert route_target(_warm_target(), slug="sixth_street", review_dir=tmp_path) == EXISTING_PARTNER
    assert route_target(_recovery_target(), slug="point72", review_dir=tmp_path) == LAPSED_PARTNER
    assert list(tmp_path.iterdir()) == []


def test_incomplete_warm_target_is_the_only_kind_queued(tmp_path):
    with pytest.raises(DraftRoutingError):
        route_target(
            _warm_target(relationship_contact_email=""),
            slug="sixth_street",
            review_dir=tmp_path,
        )
    rows = list(csv.DictReader((tmp_path / NEEDS_WARM_CSV.name).open(encoding="utf-8")))
    assert rows[0]["missing_fields"] == "relationship_contact_email"


def test_warm_draft_uses_crm_relationship_fields_and_does_not_reintroduce_blk(tmp_path):
    record = generate.generate_draft(
        None,
        None,
        load_blk_facts(),
        target=_warm_target(),
        out_dir=tmp_path,
    )
    body = record["email_body"]
    assert "My name is" not in body
    assert "Gold partnership" in body
    assert "Active" in body
    assert body.startswith("Hi Maddy,")
    assert record["owner"] == "jamari"
    assert record["contact"]["owner"] == "jamari"
    assert record["subject"] is None
    assert record["subject_status"] == "required_but_unset"
    assert record["validator_results"]["status"] == "pass"
    assert "Jamari!r21" not in body
    assert "relationship_tier: Gold" in record["evidence_block"]
    assert_no_em_dash(body)


def test_recovery_draft_acknowledges_lapse_and_exact_crm_reason(tmp_path):
    record = generate.generate_draft(
        None,
        None,
        load_blk_facts(),
        target=_recovery_target(),
        out_dir=tmp_path,
    )
    body = record["email_body"]
    assert body.startswith("Hi Kelli,")
    assert "prior Diamond partnership" in body
    assert "Not Renewing" in body
    assert "Budget cuts; cannot re-sponsor." in body
    assert "as if this were a first conversation" in body
    assert "Archive!r11" not in body
    assert "relationship_decline_reason: Budget cuts; cannot re-sponsor." in record[
        "evidence_block"
    ]
    assert_no_em_dash(body)


def test_relationship_field_drift_is_blocked():
    target = _warm_target()
    fields = {
        **generate.fixed_fields_from_facts(load_blk_facts()),
        **generate.relationship_fields_from_target(target),
    }
    fields["relationship_tier"] = "Platinum"
    with pytest.raises(generate.DraftGenerationError, match="targets.csv"):
        generate.assert_relationship_fields_match_target(fields, target)


def test_cold_generation_requires_a_human_paragraph(hooks, blk_facts, tmp_path):
    artifact = {
        "firm": "Sixth Street",
        "firm_slug": "sixth_street",
        "alignment_hooks": hooks,
        "firm_claim_sources": [hook["source_url"] for hook in hooks],
    }
    contact = ContactRecord(
        name="Maddy Livingston",
        owner="jamari",
        email="mlivingston@sixthstreet.com",
        contact_provenance=build_contact_provenance(method="test"),
    )
    with pytest.raises(generate.DraftGenerationError, match="human-authored"):
        generate.generate_draft(
            artifact,
            contact,
            blk_facts,
            out_dir=tmp_path,
        )


def test_crm_note_punctuation_is_normalized_without_rewriting_the_words():
    assert safe_crm_text("Budget cuts — cannot re-sponsor.") == (
        "Budget cuts; cannot re-sponsor."
    )


def test_derivation_writes_relationship_trace_and_canonical_owner(tmp_path):
    row = CrmRow(
        "Jamari",
        21,
        "Sixth Street Partners",
        "Active",
        "Sep 8, 2026",
        ["Maddy"],
        True,
        record_id="S048",
        tier="Gold",
        emails=["mlivingston@sixthstreet.com"],
    )
    derivation = Derivation("Sixth Street")
    derivation.contact_status = EXISTING_PARTNER
    derivation.has_known_contact = True
    derivation.deciding = row
    path = tmp_path / "targets.csv"
    rows = [{"firm": "Sixth Street", "owner": "Jamari"}]
    write_targets(path, rows, ["firm", "owner"], {"Sixth Street": derivation})
    written = next(csv.DictReader(path.open(encoding="utf-8")))
    assert written["owner"] == "jamari"
    assert written["relationship_tier"] == "Gold"
    assert written["relationship_expiration"] == date(2026, 9, 8).isoformat()
    assert written["relationship_crm_source"] == "Jamari!r21"


@pytest.mark.parametrize("slug", ["sixth_street", "advent", "point72"])
def test_real_phase4b_target_has_complete_relationship_content(slug):
    target = generate.load_target_by_slug(slug)
    assert route_target(target, slug=slug) == target["contact_status"]
    assert target["owner"] == "jamari"


def test_every_shipped_target_contact_and_draft_record_has_a_valid_owner():
    with (PROJECT_ROOT / "data" / "targets.csv").open(newline="", encoding="utf-8") as fh:
        assert {row["owner"] for row in csv.DictReader(fh)} <= {"jamari", "fola"}

    for path in (PROJECT_ROOT / "contacts" / "out").glob("*.csv"):
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                assert row["owner"] in {"jamari", "fola"}, path

    for path in (PROJECT_ROOT / "review" / "drafts").glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["owner"] in {"jamari", "fola"}, path
        assert record["contact"]["owner"] == record["owner"], path
