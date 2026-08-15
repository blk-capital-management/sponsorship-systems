"""Shared pytest fixtures."""

import sys
from pathlib import Path

# Ensure the project root is on sys.path so `import common.*` works without install
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402


@pytest.fixture
def blk_facts() -> dict:
    """A fully populated blk_facts, as it will look once member_count is filled."""
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
def compliance() -> dict:
    return {
        "opt_out_line": (
            "Reply with UNSUBSCRIBE to opt out of future outreach from "
            "BLK Capital Management."
        ),
        "uk_eu": {"legitimate_interest_basis": "B2B outreach to a named professional."},
    }


@pytest.fixture
def hooks() -> list[dict]:
    """Realistic hooks, shaped like what research/fetch.py actually emits."""
    return [
        {
            "text": (
                "Sixth Street's Homegrown Talent model begins with our Early Careers "
                "programs, opportunities for undergraduate students to connect with "
                "and learn from Sixth Street professionals."
            ),
            "source_url": "https://sixthstreet.com/careers/",
            "quote": "Homegrown Talent model begins with our Early Careers programs.",
            "basis": "campus_or_early_careers_programs",
        },
        {
            "text": "People at Sixth Street are intellectually curious by nature.",
            "source_url": "https://sixthstreet.com/values-and-culture/",
            "quote": "People at Sixth Street are intellectually curious by nature.",
            "basis": "values_themes",
        },
    ]
