"""Acceptance test 2: a null field in blk_facts.json blocks generation and names it."""

import copy

import pytest

from common.config import load_blk_facts
from common.preflight import PreflightError, check_ready, missing_fields


def test_null_member_count_blocks_and_names_the_field(blk_facts, compliance):
    blk_facts["member_count"] = None
    with pytest.raises(PreflightError) as exc:
        check_ready(blk_facts, compliance)
    assert "blk_facts.member_count" in str(exc.value)


def test_nested_null_is_named_by_dotted_path(blk_facts, compliance):
    blk_facts["fall_conference"]["host"] = None
    with pytest.raises(PreflightError) as exc:
        check_ready(blk_facts, compliance)
    assert "blk_facts.fall_conference.host" in str(exc.value)


def test_missing_opt_out_line_blocks(blk_facts, compliance):
    """The footer carries a reply-based opt-out and no physical address."""
    compliance["opt_out_line"] = None
    with pytest.raises(PreflightError) as exc:
        check_ready(blk_facts, compliance)
    assert "compliance.opt_out_line" in str(exc.value)


def test_mailing_address_is_not_required(blk_facts, compliance):
    """No physical address is collected or required anywhere in the pipeline."""
    compliance.pop("mailing_address", None)
    check_ready(blk_facts, compliance)
    assert not any("mailing_address" in f for f in missing_fields(blk_facts, compliance))


def test_all_blocking_fields_are_reported_at_once(blk_facts, compliance):
    """Fixing one field at a time across three runs is a bad experience."""
    blk_facts["member_count"] = None
    blk_facts["universities"] = ""
    compliance["opt_out_line"] = None
    blocking = missing_fields(blk_facts, compliance)
    assert set(blocking) == {
        "blk_facts.member_count",
        "blk_facts.universities",
        "compliance.opt_out_line",
    }


def test_empty_list_counts_as_blank(blk_facts, compliance):
    blk_facts["placement_verticals"] = []
    assert "blk_facts.placement_verticals" in missing_fields(blk_facts, compliance)


def test_fully_populated_config_passes(blk_facts, compliance):
    check_ready(blk_facts, compliance)


def test_shipped_config_is_complete():
    """member_count is filled and no address is required, so nothing blocks."""
    assert missing_fields(copy.deepcopy(load_blk_facts()), None) == []


def test_shipped_member_count_is_a_real_value():
    """Guards against member_count silently reverting to null."""
    assert load_blk_facts()["member_count"] == "1500+"
