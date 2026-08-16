"""Email pattern inference from published addresses.

Rule 3: no LinkedIn, ever. Patterns are derived only from addresses a firm has
already published itself: press releases, investor relations pages, media
contact pages, published bios, conference programs.

A pattern with no source_url is not a weaker pattern, it is not a pattern. It is
dropped at load time by common.config.load_email_patterns and rejected again
here, because an address built from an unsourced guess is an invented fact
wearing an @ sign.

Usage:
    from contacts.patterns import infer_pattern, render_address
    entry = infer_pattern("john.smith@acme.com", "John Smith", "acme.com", url)
    render_address(entry["pattern"], "Jane", "Doe")   -> "jane.doe@acme.com"
"""

import re
import unicodedata
from typing import Any

from common.logging import get_logger

log = get_logger("contacts.patterns")

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")

# Addresses that belong to a function, not a person, so they teach nothing about
# how the firm builds personal addresses.
ROLE_LOCAL_PARTS = frozenset({
    "info", "contact", "media", "press", "ir", "investorrelations", "investor",
    "careers", "jobs", "recruiting", "recruitment", "hr", "admin", "support",
    "hello", "enquiries", "inquiries", "general", "office", "help", "sales",
    "marketing", "webmaster", "postmaster", "noreply", "no-reply", "privacy",
    "compliance", "legal", "talent", "earlytalent", "campus", "university",
})

# Every format this module can recognize or render, in the order it tests them.
PATTERN_TEMPLATES: tuple[str, ...] = (
    "{first}.{last}",
    "{f}{last}",
    "{first}{last}",
    "{first}_{last}",
    "{last}{f}",
    "{f}.{last}",
    "{first}{l}",
    "{last}.{first}",
    "{first}",
)


def normalize_name_part(value: str) -> str:
    """Lowercase, strip accents and anything that cannot appear in a local part."""
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", folded.lower())


def split_name(full_name: str) -> tuple[str, str] | None:
    """Return (first, last) from a display name, or None if it is unusable."""
    cleaned = re.sub(r"\b(Dr|Mr|Mrs|Ms|Mx|Prof|Jr|Sr|II|III|IV|PhD|CFA|MBA)\b\.?",
                     " ", full_name, flags=re.IGNORECASE)
    parts = [normalize_name_part(p) for p in cleaned.split()]
    parts = [p for p in parts if len(p) > 1]
    if len(parts) < 2:
        return None
    return parts[0], parts[-1]


# Role words that appear inside a compound inbox name, e.g. "sixthstreetmedia".
# Substring matching catches these; exact matching does not.
ROLE_SUBSTRINGS = (
    "media", "press", "recruit", "careers", "talent", "investorrelations",
    "newsroom", "enquir", "inquir", "support", "webmaster", "noreply",
    "no-reply", "donotreply", "compliance", "privacy",
)

# Placeholder addresses left in contact forms and templates.
PLACEHOLDER_LOCAL_PARTS = frozenset({"example", "youremail", "email", "name",
                                     "firstname", "lastname", "user", "test"})


def is_role_address(local_part: str) -> bool:
    """Whether an address is a shared inbox or placeholder rather than a person's."""
    normalized = normalize_name_part(local_part)
    if normalized in ROLE_LOCAL_PARTS or normalized in PLACEHOLDER_LOCAL_PARTS:
        return True
    # A compound inbox like "sixthstreetmedia@" is a function address wearing a
    # firm name, and exact matching sails straight past it.
    return any(word in normalized for word in ROLE_SUBSTRINGS)


def render_address(pattern: str, first: str, last: str, domain: str | None = None) -> str:
    """Fill a pattern template with a person's name.

    Accepts either a bare template ("{f}{last}") or a full pattern that already
    carries its domain ("{f}{last}@acme.com").
    """
    first_n, last_n = normalize_name_part(first), normalize_name_part(last)
    if not first_n or not last_n:
        raise ValueError(f"cannot build an address from name parts {first!r} {last!r}")

    template, _, pattern_domain = pattern.partition("@")
    target_domain = domain or pattern_domain
    if not target_domain:
        raise ValueError(f"pattern {pattern!r} has no domain and none was supplied")

    local = (
        template.replace("{first}", first_n)
        .replace("{last}", last_n)
        .replace("{f}", first_n[0])
        .replace("{l}", last_n[0])
    )
    if "{" in local:
        raise ValueError(f"unrecognized token in pattern {pattern!r}")
    return f"{local}@{target_domain}".lower()


def detect_template(address: str, first: str, last: str) -> str | None:
    """Return the template that reproduces this address, or None."""
    local = address.split("@")[0].lower()
    for template in PATTERN_TEMPLATES:
        try:
            candidate = render_address(template, first, last, "x")
        except ValueError:
            continue
        if candidate.split("@")[0] == local:
            return template
    return None


def infer_pattern(
    address: str,
    person_name: str,
    domain: str,
    source_url: str,
    *,
    derived_from: str = "",
) -> dict[str, Any] | None:
    """Derive a pattern entry from one published address belonging to one person.

    Returns None when the address teaches nothing: wrong domain, shared inbox,
    unparseable name, or a local part that no known template reproduces.
    """
    if not source_url:
        # Enforced here as well as at load time, so no caller can construct an
        # unsourced pattern by going around the config loader.
        log.debug("Refusing to infer a pattern from %s with no source_url.", address)
        return None

    match = EMAIL_RE.search(address)
    if not match:
        return None
    address_domain = match.group(1).lower().removeprefix("www.")
    if address_domain != domain.lower().removeprefix("www."):
        log.debug("Address %s is not on %s. Ignoring.", address, domain)
        return None

    local_part = address.split("@")[0]
    if is_role_address(local_part):
        log.debug("%s is a shared inbox, not a person. Ignoring.", address)
        return None

    names = split_name(person_name)
    if not names:
        return None

    template = detect_template(address, *names)
    if not template:
        log.debug("No known template reproduces %s for %s.", address, person_name)
        return None

    return {
        "pattern": f"{template}@{address_domain}",
        "confidence": 0.75,
        "source_url": source_url,
        "derived_from": derived_from or f"published address for {person_name}",
        "observed_examples": 1,
        "evidence": [{"address": address.lower(), "name": person_name,
                      "source_url": source_url}],
    }


def confirmed_pattern_entry(
    email_format: str, source_url: str, domain: str
) -> dict[str, Any] | None:
    """Build a pattern entry from a format a human confirmed at intake.

    Ranked above an inferred pattern because a person read it off a published
    page and vouched for it. Its practical value is that it removes any reason to
    ask a paid provider for this domain's pattern, so the discovery path spends
    nothing on it.

    Returns None unless both the format and its source URL are present. Rule 1
    holds here exactly as it does for firm claims: an unsourced pattern is not a
    weaker pattern, it is not a pattern.
    """
    email_format = str(email_format or "").strip()
    source_url = str(source_url or "").strip()
    if not email_format or not source_url:
        return None

    try:
        render_address(email_format, "Jane", "Doe", domain or None)
    except ValueError as exc:
        log.warning("Ignoring unusable confirmed format %r: %s", email_format, exc)
        return None

    pattern = email_format if "@" in email_format else f"{email_format}@{domain}"
    return {
        "pattern": pattern,
        "confidence": 0.95,
        "source_url": source_url,
        "derived_from": "format confirmed by a human at intake",
        "observed_examples": 1,
        "provider": "human",
        "evidence": [],
    }


def merge_observations(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Combine pattern observations for one domain into a single entry.

    Agreement across independent pages is the only thing that should raise
    confidence. One press release is a guess that happened to parse.
    """
    if not entries:
        return None

    by_pattern: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_pattern.setdefault(entry["pattern"], []).append(entry)

    best_pattern, supporting = max(by_pattern.items(), key=lambda kv: len(kv[1]))

    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in supporting:
        for item in entry.get("evidence", []):
            if item["address"] not in seen:
                seen.add(item["address"])
                evidence.append(item)

    distinct_sources = len({item["source_url"] for item in evidence})
    confidence = min(0.95, 0.6 + 0.15 * len(evidence) + 0.05 * (distinct_sources - 1))

    # Conflicting formats on one domain mean the firm is inconsistent, or one
    # observation was misread. Either way the pattern is less trustworthy.
    if len(by_pattern) > 1:
        confidence -= 0.2

    return {
        "pattern": best_pattern,
        "confidence": round(max(0.0, confidence), 2),
        "source_url": evidence[0]["source_url"],
        "derived_from": (
            f"{len(evidence)} published address(es) across {distinct_sources} "
            f"page(s) on the firm's own domain"
        ),
        "observed_examples": len(evidence),
        "conflicting_formats": sorted(by_pattern) if len(by_pattern) > 1 else [],
        "evidence": evidence,
    }
