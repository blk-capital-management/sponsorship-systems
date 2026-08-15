"""Two-user owner-lane validation for BLK Bridge records."""

VALID_OWNERS = frozenset({"jamari", "fola"})


def normalize_owner(value: object) -> str:
    """Return the canonical lowercase owner or raise on an unknown lane."""
    owner = str(value or "").strip().lower()
    if owner not in VALID_OWNERS:
        raise ValueError(
            f"Invalid owner {value!r}; expected one of {', '.join(sorted(VALID_OWNERS))}."
        )
    return owner
