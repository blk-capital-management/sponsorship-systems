"""Find the right recruiting and human capital contacts at a target firm.

Two things happen here, and both are constrained by rule 3:

  1. Named people are found only in the firm's own published pages: team pages,
     press releases, media contacts, bios. No LinkedIn, ever. common/http.py
     refuses linkedin.com at the request layer so this cannot drift.

  2. Addresses are produced by pattern inference. A pattern is only recorded
     when a real published address on that domain supports it, and the page it
     came from must pass the same identity check the crawler applies, so a
     pattern is never learned from a page belonging to a different company.

Nothing written here is deliverable yet. contacts/verify.py scores every address
and drops the ones below threshold before they reach the queue.

Usage:
    python contacts/discover.py --firm "Balyasny Asset Management"
    python contacts/discover.py --all --owner Jamari
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.config import (  # noqa: E402
    CONFIG_DIR,
    PROJECT_ROOT as CFG_ROOT,
    load_settings,
    resolve_path,
)
from common.http import Fetcher  # noqa: E402
from common.identity import identify_page, looks_bot_blocked  # noqa: E402
from common.logging import get_logger  # noqa: E402
from common.owners import normalize_owner  # noqa: E402
from common.slugify import firm_slug  # noqa: E402
from contacts import patterns as pattern_lib  # noqa: E402
from contacts.gate import evaluate_pre_hunter_gate, log_skip  # noqa: E402
from research import extract  # noqa: E402
from research.fetch import find_target, load_targets  # noqa: E402

log = get_logger("contacts.discover")

EMAIL_PATTERNS_PATH = CONFIG_DIR / "email_patterns.json"

# Firm types where Human Capital sits closer to the budget than HR does, so it
# outranks the campus-specific titles rather than trailing them.
HUMAN_CAPITAL_TITLE = "Human Capital"

# Titles that mean the opposite of what we want despite matching keywords:
# "Head of Talent" at a portfolio company, or a recruiter for lateral hires.
TITLE_NEGATIVES = ("portfolio company", "executive search", "lateral",
                   "board search", "client talent")

# Pages likely to publish a named person with an address.
CONTACT_PAGE_KEYWORDS = (
    "team", "our-team", "people", "our-people", "leadership", "contact",
    "contact-us", "media", "media-contacts", "press", "press-contacts",
    "newsroom", "about-us", "professionals",
)


def title_priority(settings: dict[str, Any], firm_type: str) -> list[str]:
    """Return target titles in priority order for this firm's type."""
    cfg = settings["contacts"]
    titles = list(cfg["title_priority"])
    promote = {t.lower() for t in cfg.get("human_capital_first_for_types", [])}

    if firm_type and firm_type.lower() in promote and HUMAN_CAPITAL_TITLE in titles:
        titles.remove(HUMAN_CAPITAL_TITLE)
        titles.insert(0, HUMAN_CAPITAL_TITLE)
        log.debug("PE/credit firm: promoted %s above campus titles.", HUMAN_CAPITAL_TITLE)
    return titles


def rank_title(title: str, priority: list[str]) -> int | None:
    """Return a title's rank, lower is better, or None if it is not a target."""
    lowered = title.lower()
    if any(negative in lowered for negative in TITLE_NEGATIVES):
        return None
    for index, target in enumerate(priority):
        if target.lower() in lowered:
            return index
    # Generic HR is a fallback, never a target in its own right.
    if re.search(r"\bhuman resources\b|\bHR\b", title, re.IGNORECASE):
        return len(priority)
    return None


# ── Harvesting published addresses ────────────────────────────────────────────

def _name_near(text: str, position: int, window: int = 160) -> str | None:
    """Find a person's name in the text immediately around an address."""
    segment = text[max(0, position - window):position]
    names = re.findall(r"\b([A-Z][a-z]{1,15}(?:\s+[A-Z][a-z']{1,15}){1,2})\b", segment)
    return names[-1] if names else None


def harvest_addresses(text: str, source_url: str) -> list[tuple[str, str]]:
    """Return (address, nearby_person_name) pairs found in page text."""
    found: list[tuple[str, str]] = []
    for match in pattern_lib.EMAIL_RE.finditer(text):
        address = match.group(0)
        if pattern_lib.is_role_address(address.split("@")[0]):
            continue
        name = _name_near(text, match.start())
        if name:
            found.append((address, name))
    if found:
        log.debug("Found %s candidate address(es) at %s", len(found), source_url)
    return found


def contact_page_urls(fetcher: Fetcher, domain: str, refresh: bool) -> list[str]:
    """Pick on-domain pages most likely to publish a named person's address."""
    from research.fetch import fetch_landing

    landing = fetch_landing(fetcher, domain, refresh)
    if not landing.ok:
        return []

    from urllib.parse import urlparse

    home_host = (urlparse(landing.url).netloc or "").lower().removeprefix("www.")
    scored: list[tuple[int, str]] = []
    for url, anchor in extract.page_links(landing.html, landing.url):
        host = (urlparse(url).netloc or "").lower().removeprefix("www.")
        if host != home_host:
            continue
        score = extract.score_link(url, anchor, CONTACT_PAGE_KEYWORDS)
        if score > 0:
            scored.append((score, url))

    scored.sort(key=lambda pair: -pair[0])
    ordered = [landing.url] + [url for _, url in scored]

    seen: set[str] = set()
    unique = [u for u in ordered if not (u in seen or seen.add(u))]
    return unique[:5]


def learn_pattern(
    fetcher: Fetcher, firm: str, domain: str, refresh: bool
) -> dict[str, Any] | None:
    """Derive this domain's email pattern from its own published addresses.

    Every page is identity-checked first. Learning a pattern from a page that
    belongs to a different company is the same failure as bam.com, except the
    output is an address that reaches a real person at the wrong firm.
    """
    observations: list[dict[str, Any]] = []

    for url in contact_page_urls(fetcher, domain, refresh):
        page = fetcher.get(url, use_cache=not refresh)
        if not page.ok:
            continue

        if looks_bot_blocked(page.html, page.status):
            log.info("  %s served a bot challenge. Skipping.", url)
            continue

        mismatch = identify_page(firm, domain, page.html, page.status)
        if mismatch:
            log.warning("  Skipping %s: %s", url, mismatch)
            continue

        text = extract.page_text(page.html)
        for address, name in harvest_addresses(text, page.url):
            entry = pattern_lib.infer_pattern(
                address, name, domain, page.url,
                derived_from=f"address published for {name} on the firm's own site",
            )
            if entry:
                log.info("  Pattern evidence: %s -> %s", address, entry["pattern"])
                observations.append(entry)

    merged = pattern_lib.merge_observations(observations)
    if not merged:
        log.info("  No published address found on %s. No pattern inferred.", domain)
    return merged


def learn_pattern_from_provider(
    settings: dict[str, Any], domain: str
) -> dict[str, Any] | None:
    """Ask the configured licensed provider for this domain's pattern.

    Used only after the firm's own site yields nothing. Returns None when no
    provider is configured, so the pipeline runs without a key and simply
    produces fewer addresses.
    """
    from contacts.providers import ProviderNotConfigured, build_provider

    try:
        provider = build_provider(settings)
    except ProviderNotConfigured as exc:
        log.info("  No verification provider available for pattern lookup: %s", exc)
        return None

    # Show what this run has spent, and what the account has left, before
    # spending more.
    if hasattr(provider, "guard"):
        provider.guard.print_budget(
            provider.account() if hasattr(provider, "account") else None
        )

    entry = provider.discover_pattern(domain)
    if entry:
        log.info("  Pattern from %s: %s (%s sourced address(es))",
                 provider.name, entry["pattern"], entry["observed_examples"])
    else:
        log.info("  %s returned no sourced pattern for %s.", provider.name, domain)
    return entry


def learn_pattern_from_provider_instance(
    provider: Any, domain: str
) -> dict[str, Any] | None:
    """Use an already-guarded provider supplied by a storage adapter.

    The command-line path builds its provider from settings. A concurrent
    dashboard run needs a guard whose target scope and audit sink come from the
    authenticated database lane, so it supplies the provider explicitly while
    preserving the same provider method and result checks.
    """
    if hasattr(provider, "guard"):
        provider.guard.print_budget(
            provider.account() if hasattr(provider, "account") else None
        )
    entry = provider.discover_pattern(domain)
    if entry:
        log.info("  Pattern from %s: %s (%s sourced address(es))",
                 provider.name, entry["pattern"], entry["observed_examples"])
    else:
        log.info("  %s returned no sourced pattern for %s.", provider.name, domain)
    return entry


def save_pattern(domain: str, entry: dict[str, Any]) -> None:
    """Record a learned pattern in config/email_patterns.json."""
    raw = json.loads(EMAIL_PATTERNS_PATH.read_text(encoding="utf-8"))
    raw.setdefault("patterns", {})[domain] = entry
    EMAIL_PATTERNS_PATH.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    log.info("  Recorded pattern for %s: %s (confidence %.2f)",
             domain, entry["pattern"], entry["confidence"])


# ── Discovery ─────────────────────────────────────────────────────────────────

def people_from_provider_evidence(
    entry: dict[str, Any] | None, priority: list[str]
) -> list[dict[str, Any]]:
    """Turn a provider's sourced addresses into contact rows.

    Only people whose title matches the target list are kept. The provider
    returns whoever it has, which at these firms is mostly investment staff, and
    emailing a Managing Director about a student partnership wastes the contact.

    The address here is the one the provider published, not one rendered from a
    pattern, so it is used verbatim and still goes through verification.
    """
    if not entry:
        return []

    rows: list[dict[str, Any]] = []
    for item in entry.get("evidence", []):
        position = item.get("position") or ""
        rank = rank_title(position, priority)
        if rank is None:
            continue
        name = (item.get("name") or "").strip()
        if not name or pattern_lib.split_name(name) is None:
            continue
        rows.append({
            "name": name,
            "title": position[:120],
            "title_rank": rank,
            "source_url": item.get("source_url", ""),
            "provider_email": item.get("address", ""),
        })
    rows.sort(key=lambda r: r["title_rank"])
    return rows


def research_artifact_path(slug: str, settings: dict[str, Any]) -> Path:
    return resolve_path(settings["research"]["out_dir"]) / f"{slug}.json"


def load_artifact(slug: str, settings: dict[str, Any]) -> dict[str, Any] | None:
    path = research_artifact_path(slug, settings)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def find_named_contacts(
    fetcher: Fetcher, firm: str, domain: str, priority: list[str], refresh: bool
) -> list[dict[str, Any]]:
    """Find people on the firm's own pages whose titles match the target list."""
    contacts: list[dict[str, Any]] = []
    seen: set[str] = set()

    for url in contact_page_urls(fetcher, domain, refresh):
        page = fetcher.get(url, use_cache=not refresh)
        if not page.ok or looks_bot_blocked(page.html, page.status):
            continue
        if identify_page(firm, domain, page.html, page.status):
            continue

        text = extract.page_text(page.html)
        lines = text.splitlines()

        # Team pages usually render a name and its title as adjacent blocks, not
        # one line, so same-line-only pairing misses almost everyone. The window
        # stays tight: one line either side. Widening it starts inventing links
        # between people and roles that the page never made.
        for index, line in enumerate(lines):
            rank = rank_title(line, priority)
            if rank is None:
                continue

            window = lines[max(0, index - 1):index + 2]
            for candidate in window:
                names = re.findall(
                    r"\b([A-Z][a-z]{1,15}(?:\s+[A-Z][a-z']{1,15}){1,2})\b", candidate
                )
                for name in names:
                    if pattern_lib.split_name(name) is None or name in seen:
                        continue
                    # A title line that is itself the name line adds nothing.
                    if rank_title(name, priority) is not None:
                        continue
                    seen.add(name)
                    contacts.append({
                        "name": name,
                        "title": line.strip()[:120],
                        "title_rank": rank,
                        "source_url": page.url,
                    })

    contacts.sort(key=lambda c: c["title_rank"])
    return contacts


def discover_target_contacts(
    target: dict[str, Any],
    artifact: dict[str, Any],
    *,
    refresh: bool = False,
    provider: Any = None,
    persist_pattern: bool = False,
    settings: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run the validated discovery algorithm against in-memory records.

    This is the storage-neutral core used by both the CSV command and the
    Supabase dashboard. Callers must run `evaluate_pre_hunter_gate` first. The
    artifact gate is repeated here so a low-confidence firm cannot reach a
    provider even if a new orchestration path forgets the check.
    """
    settings = settings or load_settings()
    if not artifact.get("alignment_hooks") or artifact.get("confidence") == "low":
        log.warning("  Research is %s with %s hook(s), so this firm is in the manual "
                    "queue. No contacts discovered.",
                    artifact.get("confidence"), len(artifact.get("alignment_hooks", [])))
        return []

    domain = str(target.get("domain") or "").strip()
    fetcher = Fetcher(settings["research"])
    priority = title_priority(settings, str(target.get("firm_type") or ""))

    entry = learn_pattern(fetcher, str(target.get("firm") or ""), domain, refresh)
    if not entry:
        entry = (
            learn_pattern_from_provider_instance(provider, domain)
            if provider is not None
            else learn_pattern_from_provider(settings, domain)
        )
    if entry and persist_pattern:
        save_pattern(domain, entry)

    people = find_named_contacts(
        fetcher, str(target.get("firm") or ""), domain, priority, refresh
    )
    log.info("  %s person/people found on the firm's own site.", len(people))
    people += people_from_provider_evidence(entry, priority)
    log.info("  %s person/people total after provider evidence.", len(people))

    rows: list[dict[str, Any]] = []
    for raw_person in people:
        person = dict(raw_person)
        published = person.pop("provider_email", "")
        if published:
            rows.append({
                **person,
                "email": published,
                "pattern": entry["pattern"] if entry else "",
                "pattern_confidence": entry["confidence"] if entry else "",
                "pattern_source_url": person.get("source_url", ""),
            })
            continue

        names = pattern_lib.split_name(person["name"])
        if not names or not entry:
            rows.append({**person, "email": "", "pattern": "",
                         "pattern_confidence": "", "pattern_source_url": ""})
            continue
        try:
            address = pattern_lib.render_address(entry["pattern"], *names)
        except ValueError as exc:
            log.debug("  Could not build an address for %s: %s", person["name"], exc)
            address = ""
        rows.append({
            **person,
            "email": address,
            "pattern": entry["pattern"],
            "pattern_confidence": entry["confidence"],
            "pattern_source_url": entry["source_url"],
        })

    owner = normalize_owner(target.get("owner"))
    for row in rows:
        row["owner"] = owner
    return rows


def discover_firm(firm: str, *, refresh: bool = False) -> list[dict[str, Any]]:
    """Discover contacts for one firm and write contacts/out/<slug>_contacts.csv."""
    settings = load_settings()
    target = find_target(firm, load_targets())
    slug = firm_slug(target["firm"])
    domain = target["domain"].strip()

    log.info("Discovering contacts for %s (%s)", target["firm"], domain)

    # The pre-Hunter gate runs before anything is fetched or looked up, so a
    # skip costs nothing at all. targets.csv is its only input; the CRM is never
    # read at runtime.
    decision = evaluate_pre_hunter_gate(target)
    if decision.skip:
        log_skip(decision, domain)
        log.info("  No provider call made and no contact file rewritten. The "
                 "contact already on file stands.")
        return []

    # Rule 1, applied to people rather than prose: a firm with no usable research
    # is a firm we have nothing to say to, so it gets no contacts either. Falling
    # back to a guessed pattern here would smuggle an unverified address into the
    # queue for a firm already routed to manual review.
    artifact = load_artifact(slug, settings)
    if artifact is None:
        log.warning("  No research artifact at %s. No contacts discovered.",
                    research_artifact_path(slug, settings).name)
        write_contacts(slug, [], settings)
        return []
    if not artifact["alignment_hooks"] or artifact["confidence"] == "low":
        log.warning("  Research is %s with %s hook(s), so this firm is in the manual "
                    "queue. No contacts discovered.",
                    artifact["confidence"], len(artifact["alignment_hooks"]))
        write_contacts(slug, [], settings)
        return []

    rows = discover_target_contacts(
        target, artifact, refresh=refresh, persist_pattern=True, settings=settings
    )
    write_contacts(slug, rows, settings)
    return rows


def write_contacts(slug: str, rows: list[dict[str, Any]], settings: dict[str, Any]) -> Path:
    """Write the per-firm contact file. Always written, even when empty.

    Discovery URLs land in the contact_provenance_* columns, never in a column
    named source_url. Hunter attributes most addresses to a LinkedIn-scoped
    Google search, which is a true statement about discovery and an unusable
    firm claim, so the two namespaces do not share a column name.
    """
    import csv

    out_dir = CFG_ROOT / "contacts" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slug}_contacts.csv"

    from contacts.record import CONTACT_FIELDS, to_contact_row

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CONTACT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(to_contact_row(row))

    log.info("  Wrote %s row(s) -> contacts/out/%s", len(rows), path.name)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--firm", help="Firm name as it appears in data/targets.csv")
    group.add_argument("--all", action="store_true", help="Every target")
    parser.add_argument("--owner", help="With --all, restrict to one owner")
    parser.add_argument("--refresh", action="store_true", help="Bypass the HTML cache")
    args = parser.parse_args(argv)

    if args.firm:
        firms = [args.firm]
    else:
        targets = load_targets()
        if args.owner:
            targets = [t for t in targets
                       if t.get("owner", "").lower() == args.owner.lower()]
        firms = [t["firm"] for t in targets]

    failures = 0
    for name in firms:
        try:
            discover_firm(name, refresh=args.refresh)
        except Exception as exc:
            failures += 1
            log.error("Discovery failed for %s: %s: %s", name, type(exc).__name__, exc)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
