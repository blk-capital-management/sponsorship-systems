"""Does this page actually belong to the firm we think it does?

A plausible-looking domain owned by a different company is the worst failure
this pipeline can have: it produces confident, fully-cited research about the
wrong business, and every individual claim checks out. bam.com is Royal BAM
Group, a Dutch construction firm, not Balyasny Asset Management.

The same check guards three places:
  - research/fetch.py     before trusting a crawl
  - tools/verify_domains  before trusting a hand-entered domain
  - contacts/discover.py  before inferring an email pattern from a bio page

Usage:
    from common.identity import identify_page, distinctive_tokens
    reason = identify_page("Balyasny Asset Management", "bam.com", html)
    if reason:
        ...  # reason explains the mismatch
"""

import re

from bs4 import BeautifulSoup

from common.slugify import firm_slug

# Tokens too short to distinguish one firm from another.
MIN_TOKEN_LENGTH = 4


def distinctive_tokens(firm: str) -> list[str]:
    """Return the parts of a firm name that actually identify it.

    firm_slug already strips "capital", "partners", "group" and friends, which
    is precisely the vocabulary that would match any finance website.
    """
    return [t for t in firm_slug(firm).split("_") if len(t) >= MIN_TOKEN_LENGTH]


def page_identity_text(html: str, domain: str = "") -> str:
    """Pull the identifying strings out of a page: title, og:site_name, domain."""
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""

    site_name = ""
    for attrs in ({"property": "og:site_name"}, {"name": "application-name"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            site_name = tag["content"]
            break

    return f"{title} {site_name} {domain}"


def matches_firm(firm: str, text: str) -> bool:
    """Whether any distinctive token of the firm name appears in the text."""
    tokens = distinctive_tokens(firm)
    if not tokens:
        # Nothing distinctive to test against, so this check cannot judge.
        return True
    haystack = text.lower()
    return any(
        re.search(rf"\b{re.escape(t)}", haystack) or t in haystack for t in tokens
    )


# Markers left by bot-protection services when they serve a challenge instead of
# the real page. Such a page carries no firm identity, so judging it as a name
# mismatch would report a correct domain as a data error.
_BOT_WALL_MARKERS = (
    "_incapsula_resource", "incapsula incident", "imperva",
    "attention required! | cloudflare", "cf-browser-verification",
    "just a moment...", "checking your browser before accessing",
    "access denied", "request unsuccessful", "distil_r_captcha",
    "px-captcha", "perimeterx", "are you a robot",
)


def looks_bot_blocked(html: str, status: int = 200) -> bool:
    """Whether a response is a bot-protection challenge rather than the real page.

    These sites are declining automated access. The correct response is to route
    the firm to manual research, never to work around the block.
    """
    if status in (401, 403, 429):
        return True
    lowered = html[:20000].lower()
    if any(marker in lowered for marker in _BOT_WALL_MARKERS):
        return True
    # A near-empty document carrying a noindex directive is a challenge stub.
    return len(html) < 1500 and "noindex" in lowered


def identify_page(firm: str, domain: str, html: str, status: int = 200) -> str | None:
    """Return None when the page identifies as the firm, else a reason string."""
    text = page_identity_text(html, domain)
    if matches_firm(firm, text):
        return None

    if looks_bot_blocked(html, status):
        return (
            f"bot protection served a challenge page for {domain}, so its identity "
            f"could not be confirmed. This is not evidence the domain is wrong. "
            f"Confirm {firm!r} by hand and research it manually."
        )

    soup = BeautifulSoup(html, "lxml")
    presented = (soup.title.get_text(" ", strip=True) if soup.title else "") or "an unnamed site"
    return (
        f"domain identity mismatch: {domain} presents as {presented.strip()[:80]!r}, "
        f"which does not match {firm!r}. Confirm the correct domain before using "
        "this artifact."
    )
