"""Firm slugs. The join key across every pipeline stage.

Slugs must be stable: research writes research/out/<slug>.json, contacts writes
contacts/out/<slug>_contacts.csv, drafts writes drafts/out/<slug>.md. Deriving a
slug ad hoc anywhere else is how those stop lining up.

Usage:
    from common.slugify import firm_slug
    firm_slug("Balyasny Asset Mgmt (BAM)")   -> "balyasny_asset_mgmt"
"""

import re
import unicodedata

# Dropped from slugs so "Sixth Street Partners" and "Sixth Street" collide rather
# than silently producing two artifacts for one firm.
_SUFFIXES = {
    "the", "inc", "llc", "lp", "llp", "ltd", "plc", "co", "corp", "company",
    "group", "partners", "capital", "management", "mgmt", "holdings", "advisors",
    "advisers", "international", "investments", "asset", "and",
}


def firm_slug(firm: str) -> str:
    """Return a stable lowercase underscore slug for a firm name.

    Parenthetical aliases are stripped first, so "Balyasny Asset Mgmt (BAM)" and
    "Balyasny Asset Management" resolve to the same slug.
    """
    if not firm or not firm.strip():
        raise ValueError("firm name is empty")

    name = re.sub(r"\([^)]*\)", " ", firm)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^\w\s]", " ", name).lower()

    words = [w for w in name.split() if w]
    kept = [w for w in words if w not in _SUFFIXES]

    # Never return empty. A firm named only from the suffix list keeps its words.
    if not kept:
        kept = words

    return "_".join(kept)
