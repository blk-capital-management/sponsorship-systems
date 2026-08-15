"""Two provenance namespaces that must never mix.

Before this module there was one field name, `source_url`, doing two unrelated
jobs. On a research artifact it meant "the firm's own page that backs this
claim". On a contact row it meant "how Hunter found this person", which is
routinely a Google search scoped to LinkedIn. Sharing a name meant a renderer
could not tell one from the other, and the second kind must never reach an
email.

They are now separate namespaces with separate rules:

  firm_claim_sources   Direct URLs backing assertions about the firm. These are
                       citable in an evidence block. A search-engine URL or any
                       LinkedIn-scoped string is rejected, because a search
                       result page is not evidence of anything and a LinkedIn
                       scope reads as scraping (rule 3).

  contact_provenance   How a contact name or address was discovered. Accepts
                       whatever the provider reported, including LinkedIn-scoped
                       Google search strings. Carries internal_only: True, and
                       `assert_renderable` refuses to emit any structure that
                       carries that flag.

Usage:
    from common.namespaces import validate_firm_claim_source, assert_renderable
    validate_firm_claim_source("https://www.bamfunds.com/careers")
    assert_renderable(draft_payload, "email_body")
"""

import re
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlsplit, urlunsplit

from common.logging import get_logger

log = get_logger("common.namespaces")

# The marker that makes a field internal. Any structure carrying it truthy is
# refused by every renderer, whatever else it contains.
INTERNAL_ONLY_KEY = "internal_only"

# Hosts whose result pages are not evidence. A search URL says what was typed
# into a box, not what a firm published.
_SEARCH_HOSTS = re.compile(
    r"^(?:[a-z0-9-]+\.)*(?:google|bing|duckduckgo|yahoo|baidu|yandex|ecosia)\."
    r"[a-z.]+$"
)
_SEARCH_PATHS = ("/search", "/url", "/imgres")

# Matched against the percent-decoded URL, so `site%3Alinkedin.com` is caught
# alongside a plain linkedin.com host.
_LINKEDIN = re.compile(r"linkedin", re.IGNORECASE)


# A port that the scheme already implies carries no information and makes a
# reviewer-facing URL look like machine output.
_DEFAULT_PORTS = {"http": 80, "https": 443}


class ProvenanceNamespaceError(ValueError):
    """Raised when a URL is put in the wrong provenance namespace."""


class InternalOnlyLeakError(RuntimeError):
    """Raised when a renderer is handed a field marked internal_only."""


# ── Normalization ─────────────────────────────────────────────────────────────

def strip_default_port(url: str) -> str:
    """Remove :443 from an https URL and :80 from an http URL.

    Applied at capture time so a reviewer never sees `bamfunds.com:443` in an
    evidence block. Nothing else about the URL is touched, and the function is
    idempotent, so it is safe to run over already-clean data.
    """
    candidate = (url or "").strip()
    if not candidate:
        return candidate
    try:
        parts = urlsplit(candidate)
        port = parts.port
    except ValueError:
        # A malformed authority is left exactly as captured. Guessing at a fix
        # would change provenance.
        return candidate
    if port is None or _DEFAULT_PORTS.get((parts.scheme or "").lower()) != port:
        return candidate

    host = parts.hostname or ""
    if parts.username:
        credentials = parts.username
        if parts.password:
            credentials = f"{credentials}:{parts.password}"
        host = f"{credentials}@{host}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def normalize_contact_url(url: str) -> str:
    """Normalization for contact_provenance values only.

    Also turns %20 back into a space. These strings are read by a human deciding
    whether a name is real, and `site:linkedin.com%20hannah%20dinardo` is harder
    to read than the same query with spaces. Percent-encoding is left alone
    everywhere else, because decoding it in a firm claim source would change a
    URL that has to stay clickable.
    """
    return strip_default_port(url).replace("%20", " ")


# ── Firm claim sources ────────────────────────────────────────────────────────

def is_linkedin_scoped(url: str) -> bool:
    """True if the URL mentions LinkedIn anywhere, encoded or not."""
    return bool(_LINKEDIN.search(unquote(unquote(url or ""))))


def is_search_url(url: str) -> bool:
    """True if the URL is a search-engine result page rather than a document."""
    parts = urlsplit(unquote(url or ""))
    host = (parts.hostname or "").lower()
    if not _SEARCH_HOSTS.match(host):
        return False
    return parts.path.rstrip("/") in _SEARCH_PATHS or bool(parts.query)


def validate_firm_claim_source(url: str, *, where: str = "firm_claim_sources") -> str:
    """Return the URL if it may back a firm claim, otherwise raise.

    Args:
        url: The candidate source URL.
        where: Field name used in the error message.

    Raises:
        ProvenanceNamespaceError: If the URL is empty, not http(s), a search
            result page, or LinkedIn-scoped.
    """
    candidate = (url or "").strip()
    if not candidate:
        raise ProvenanceNamespaceError(f"{where}: empty URL. A claim with no URL is not a claim (rule 1).")
    if not candidate.lower().startswith(("http://", "https://")):
        raise ProvenanceNamespaceError(
            f"{where}: {candidate!r} is not a direct http(s) URL."
        )
    if is_linkedin_scoped(candidate):
        raise ProvenanceNamespaceError(
            f"{where}: {candidate!r} is LinkedIn-scoped. LinkedIn provenance belongs "
            "in contact_provenance, which is internal only, and never backs a firm "
            "claim (rule 3)."
        )
    if is_search_url(candidate):
        raise ProvenanceNamespaceError(
            f"{where}: {candidate!r} is a search result page, not a source. Cite the "
            "firm's own page (rule 1)."
        )
    return candidate


def firm_claim_source_of(item: Mapping[str, Any]) -> str:
    """Read a claim's source URL, tolerating the pre-migration key name."""
    return (item.get("firm_claim_source") or item.get("source_url") or "").strip()


def collect_firm_claim_sources(hooks: Iterable[Mapping[str, Any]]) -> list[str]:
    """Distinct, order-preserving, validated claim URLs for an artifact."""
    seen: list[str] = []
    for hook in hooks:
        url = validate_firm_claim_source(firm_claim_source_of(hook))
        if url not in seen:
            seen.append(url)
    return seen


# ── Contact provenance ────────────────────────────────────────────────────────

def build_contact_provenance(
    discovery_url: str = "",
    pattern_url: str = "",
    *,
    method: str = "",
    provider: str = "",
) -> dict[str, Any]:
    """Build the internal-only record of how a contact was found.

    No URL rules apply here. Hunter attributes most addresses to a LinkedIn-scoped
    Google search, and that is a true statement about discovery. It is kept
    because a reviewer needs to know where a name came from, and it is flagged
    internal_only so no renderer can emit it.
    """
    return {
        INTERNAL_ONLY_KEY: True,
        "discovery_url": (discovery_url or "").strip(),
        "pattern_url": (pattern_url or "").strip(),
        "method": method,
        "provider": provider,
    }


def contact_provenance_urls(provenance: Mapping[str, Any] | None) -> list[str]:
    """Every URL inside a contact_provenance block, for leak assertions."""
    if not provenance:
        return []
    return [
        str(provenance[key])
        for key in ("discovery_url", "pattern_url")
        if provenance.get(key)
    ]


# ── Render guard ──────────────────────────────────────────────────────────────

def is_internal_only(value: Any) -> bool:
    return isinstance(value, Mapping) and bool(value.get(INTERNAL_ONLY_KEY))


def assert_renderable(value: Any, where: str = "rendered output", _path: str = "") -> None:
    """Refuse to render anything carrying internal_only, at any depth.

    Raises:
        InternalOnlyLeakError: If the value, or anything nested inside it, is
            marked internal_only.
    """
    if is_internal_only(value):
        location = f"{where}.{_path}" if _path else where
        raise InternalOnlyLeakError(
            f"Refusing to render {location}: the field is marked "
            f"{INTERNAL_ONLY_KEY}. contact_provenance is for reviewers, never for "
            "anything a firm receives."
        )
    if isinstance(value, Mapping):
        for key, item in value.items():
            assert_renderable(item, where, f"{_path}.{key}" if _path else str(key))
    elif isinstance(value, (list, tuple, set)):
        for index, item in enumerate(value):
            assert_renderable(item, where, f"{_path}[{index}]" if _path else f"[{index}]")


def strip_internal_only(value: Any) -> Any:
    """Return a copy with every internal_only structure removed.

    For callers that legitimately hold a mixed record and need the emittable
    half. Dropping is explicit here so it can never happen silently mid-render.
    """
    if is_internal_only(value):
        return None
    if isinstance(value, Mapping):
        return {
            key: strip_internal_only(item)
            for key, item in value.items()
            if not is_internal_only(item)
        }
    if isinstance(value, list):
        return [strip_internal_only(item) for item in value if not is_internal_only(item)]
    return value
