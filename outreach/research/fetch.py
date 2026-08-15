"""Crawl a target firm's own domain and write a structured research artifact.

This is the only stage that touches the web (rule 6). It writes JSON and stops.
Drafting reads that JSON and never browses.

Crawl budget is four page categories on the firm's own domain: careers,
university or early careers recruiting, values or culture, and recent news.
Nothing deeper.

Usage:
    python research/fetch.py --firm "Balyasny Asset Management"
    python research/fetch.py --all --owner Jamari
    python research/fetch.py --firm "Citadel" --refresh
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.config import PROJECT_ROOT as CFG_ROOT, load_settings, resolve_path  # noqa: E402
from common.http import Fetcher, Page  # noqa: E402
from common.identity import identify_page  # noqa: E402
from common.logging import get_logger  # noqa: E402
from common.namespaces import (  # noqa: E402
    collect_firm_claim_sources,
    strip_default_port,
    validate_firm_claim_source,
)
from common.slugify import firm_slug  # noqa: E402
from research import extract  # noqa: E402

log = get_logger("research.fetch")

TARGETS_CSV = CFG_ROOT / "data" / "targets.csv"
SCHEMA_PATH = CFG_ROOT / "research" / "schema.json"

EVIDENCE_FIELDS = tuple(extract.FIELD_PATTERNS.keys())

# Hooks are drawn in this order. A live undergraduate program is a stronger
# opening than a values page, and a values page is stronger than a news item
# that may have nothing to do with talent.
HOOK_PRIORITY = (
    "campus_or_early_careers_programs",
    "existing_student_org_partnerships",
    "recruiting_timeline_signals",
    "values_themes",
    "asset_classes",
    "recent_relevant_news",
)


# ── Targets ───────────────────────────────────────────────────────────────────

def load_targets(path: Path = TARGETS_CSV) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"targets.csv not found: {path}")
    with path.open(newline="", encoding="utf-8") as fh:
        return [row for row in csv.DictReader(fh) if row.get("firm", "").strip()]


def find_target(firm: str, targets: list[dict[str, str]]) -> dict[str, str]:
    """Match a firm by exact name, then by slug, so CLI aliases work."""
    wanted = firm.strip().lower()
    for row in targets:
        if row["firm"].strip().lower() == wanted:
            return row
    wanted_slug = firm_slug(firm)
    for row in targets:
        if firm_slug(row["firm"]) == wanted_slug:
            return row
    known = ", ".join(sorted(r["firm"] for r in targets)[:8])
    raise KeyError(f"Firm '{firm}' is not in targets.csv. Known firms include: {known}...")


# ── Crawl ─────────────────────────────────────────────────────────────────────

def fetch_landing(fetcher: Fetcher, domain: str, refresh: bool) -> Page:
    """Fetch the firm's landing page, trying the bare domain then www.

    On total failure this returns the most informative attempt rather than a
    synthetic error page, so callers can still see a real HTTP status. A 403 is
    a site declining automated access, which is a different problem from a
    domain that does not resolve, and the two need different follow-up.
    """
    attempts: list[Page] = []
    for candidate in (f"https://{domain}", f"https://www.{domain}"):
        page = fetcher.get(candidate, use_cache=not refresh)
        if page.ok:
            return page
        attempts.append(page)
        log.debug("Landing attempt failed: %s (status %s, %s)",
                  candidate, page.status, page.error)

    with_status = [p for p in attempts if p.status]
    if with_status:
        return with_status[0]
    return Page(url=f"https://{domain}", status=0, html="", error="landing_unreachable")


def verify_identity(firm: str, domain: str, landing: Page) -> str | None:
    """Confirm the landing page belongs to the firm we think it does.

    Delegates to common.identity so the crawl, the targets.csv audit, and
    contact discovery all apply the same standard.
    """
    if not landing.ok:
        return None  # Unreachable pages are reported separately as gaps.
    return identify_page(firm, domain, landing.html)


def pick_category_pages(landing: Page, cfg: dict[str, Any]) -> dict[str, str]:
    """Choose the single best on-domain page per category."""
    from urllib.parse import urlparse

    home_host = (urlparse(landing.url).netloc or "").lower().removeprefix("www.")
    links = extract.page_links(landing.html, landing.url)[: cfg.get("max_link_candidates", 400)]

    on_domain = []
    for url, anchor in links:
        host = (urlparse(url).netloc or "").lower().removeprefix("www.")
        # Off-domain links are discarded. Research is about the firm's own words.
        if host == home_host and url != landing.url:
            on_domain.append((url, anchor))

    chosen: dict[str, str] = {}
    taken: set[str] = set()
    # University recruiting is resolved first: it is the most valuable category
    # and the most specific, so it should not lose its best page to /careers.
    ordered = sorted(
        cfg.get("categories", list(extract.CATEGORY_LINK_KEYWORDS)),
        key=lambda c: 0 if c == "university_recruiting" else 1,
    )
    for category in ordered:
        keywords = extract.CATEGORY_LINK_KEYWORDS.get(category, ())
        negatives = extract.CATEGORY_NEGATIVE_KEYWORDS.get(category, ())
        best_url, best_score = None, 0
        for url, anchor in on_domain:
            if url in taken:
                continue
            score = extract.score_link(url, anchor, keywords, negatives)
            if score > best_score:
                best_url, best_score = url, score
        if best_url:
            chosen[category] = best_url
            taken.add(best_url)
    return chosen


def crawl_firm(fetcher: Fetcher, target: dict[str, str], cfg: dict[str, Any],
               refresh: bool) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    """Fetch the landing page plus up to max_pages_per_firm category pages.

    Returns (pages_crawled, page_texts, blocking_reasons). A non-empty
    blocking_reasons list forces the artifact to low confidence.
    """
    domain = target["domain"].strip()
    landing = fetch_landing(fetcher, domain, refresh)

    # Normalized once, here, at capture. Every downstream URL in the artifact is
    # copied from this dict or from `texts`, so a scheme-default port cannot
    # reappear in an evidence block later.
    landing_url = strip_default_port(landing.url)

    crawled: list[dict[str, Any]] = [{
        "category": "landing",
        "url": landing_url,
        "status": landing.status,
        "from_cache": landing.from_cache,
        "error": landing.error,
    }]
    texts: dict[str, str] = {}
    blocking: list[str] = []

    if not landing.ok:
        log.warning("Landing page unreachable for %s (%s).", target["firm"], domain)
        return crawled, texts, ["landing page unreachable"]

    mismatch = verify_identity(target["firm"], domain, landing)
    if mismatch:
        log.error("%s: %s", target["firm"], mismatch)
        blocking.append(mismatch)

    texts[f"landing::{landing_url}"] = extract.page_text(landing.html)

    budget = cfg.get("max_pages_per_firm", 4)
    for category, url in list(pick_category_pages(landing, cfg).items())[:budget]:
        page = fetcher.get(url, use_cache=not refresh)

        if (not page.ok or len(extract.page_text(page.html))
                < cfg.get("min_text_chars_before_js_fallback", 400)):
            if cfg.get("playwright_fallback"):
                log.info("Thin or failed HTML for %s. Retrying with Playwright.", url)
                page = fetcher.get_rendered(url)

        page_url = strip_default_port(page.url)
        crawled.append({
            "category": category,
            "url": page_url,
            "status": page.status,
            "from_cache": page.from_cache,
            "error": page.error,
        })
        if page.ok:
            texts[f"{category}::{page_url}"] = extract.page_text(page.html)
        else:
            log.info("Could not read %s page for %s: %s",
                     category, target["firm"], page.error or page.status)

    return crawled, texts, blocking


# ── Artifact assembly ─────────────────────────────────────────────────────────

def build_hooks(evidence: dict[str, list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    """Select at most `limit` one-sentence hooks, each with its own firm_claim_source.

    Hooks are verbatim sentences from the firm's site. Drafting sees only these,
    so a hook that is not really about the firm becomes a bad email. Preferring
    distinct source pages keeps the three hooks from all restating one page.
    """
    hooks: list[dict[str, Any]] = []
    used_urls: set[str] = set()

    for pass_number in (1, 2):
        for field_name in HOOK_PRIORITY:
            if len(hooks) >= limit:
                return hooks
            for item in evidence.get(field_name, []):
                if len(hooks) >= limit:
                    return hooks
                # First pass takes one hook per source page, so three hooks are
                # not three sentences off the same careers page.
                if pass_number == 1 and item["source_url"] in used_urls:
                    continue
                if any(h["text"] == item["value"] for h in hooks):
                    continue
                hooks.append({
                    "text": item["value"],
                    # Firm namespace. Validated here so a bad URL fails at write
                    # time, not when a reviewer is reading an evidence block.
                    "firm_claim_source": validate_firm_claim_source(item["source_url"]),
                    "quote": item["quote"],
                    "basis": field_name,
                })
                used_urls.add(item["source_url"])
                break
    return hooks


def score_confidence(evidence: dict[str, list[dict[str, Any]]],
                     cfg: dict[str, Any], blocking: list[str]) -> str:
    """Confidence is how many distinct page categories yielded citable content.

    Any blocking reason, such as a domain identity mismatch, pins the result to
    low regardless of how much text was extracted. Volume of evidence about the
    wrong company is not confidence.
    """
    if blocking:
        return "low"

    categories = {
        item["category"]
        for items in evidence.values()
        for item in items
        if item.get("category") and item["category"] != "landing"
    }
    thresholds = cfg.get("confidence", {})
    count = len(categories)
    if count >= thresholds.get("high_min_categories", 3):
        return "high"
    if count >= thresholds.get("medium_min_categories", 2):
        return "medium"
    return "low"


def collect_gaps(evidence: dict[str, list[dict[str, Any]]],
                 crawled: list[dict[str, Any]]) -> list[str]:
    gaps = [f"no citable content found for {name}"
            for name in EVIDENCE_FIELDS if not evidence.get(name)]
    for page in crawled:
        if page.get("error") == "disallowed_by_robots":
            gaps.append(f"robots.txt disallowed the {page['category']} page")
        elif page.get("error"):
            gaps.append(f"could not fetch the {page['category']} page ({page['error']})")
    missing = {"careers", "university_recruiting", "values_culture", "news"} - {
        p["category"] for p in crawled if not p.get("error")
    }
    gaps += [f"no {name} page found on the firm's domain" for name in sorted(missing)]
    return gaps


def build_artifact(target: dict[str, str], crawled: list[dict[str, Any]],
                   texts: dict[str, str], cfg: dict[str, Any],
                   blocking: list[str] | None = None) -> dict[str, Any]:
    evidence: dict[str, list[dict[str, Any]]] = {name: [] for name in EVIDENCE_FIELDS}

    for key, text in texts.items():
        category, _, url = key.partition("::")
        for field_name, items in extract.extract_fields(text, url, category).items():
            evidence[field_name].extend(items)

    # Cap each field so an artifact stays reviewable.
    for field_name in evidence:
        evidence[field_name] = evidence[field_name][:5]

    blocking = blocking or []
    # A blocked artifact carries no hooks. Hooks are what drafting is allowed to
    # assert, so emitting them from an unverified domain would defeat the gate.
    hooks = [] if blocking else build_hooks(evidence, cfg.get("max_alignment_hooks", 3))

    return {
        "firm": target["firm"],
        "firm_slug": firm_slug(target["firm"]),
        "domain": target["domain"],
        "region": target.get("region", ""),
        "relationship": target.get("relationship", "prospect"),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages_crawled": crawled,
        **evidence,
        "alignment_hooks": hooks,
        "firm_claim_sources": collect_firm_claim_sources(hooks),
        "confidence": score_confidence(evidence, cfg, blocking),
        "gaps": blocking + collect_gaps(evidence, crawled),
    }


def validate_artifact(artifact: dict[str, Any]) -> None:
    """Validate against research/schema.json. A malformed artifact never ships."""
    import jsonschema

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(instance=artifact, schema=schema)


# ── Routing ───────────────────────────────────────────────────────────────────

def route_to_manual_queue(artifact: dict[str, Any], settings: dict[str, Any]) -> Path:
    """Write a firm to the manual queue. No draft is ever generated for it."""
    path = resolve_path(settings["review"]["manual_queue_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()

    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if is_new:
            writer.writerow(["firm", "firm_slug", "domain", "confidence",
                             "reason", "gaps", "artifact_path", "queued_at"])
        reason = ("no alignment hooks with a source_url"
                  if not artifact["alignment_hooks"] else "research confidence is low")
        writer.writerow([
            artifact["firm"], artifact["firm_slug"], artifact["domain"],
            artifact["confidence"], reason, "; ".join(artifact["gaps"][:6]),
            f"research/out/{artifact['firm_slug']}.json",
            artifact["fetched_at"],
        ])
    return path


def research_firm(firm: str, *, refresh: bool = False) -> dict[str, Any]:
    """Crawl one firm and write research/out/<slug>.json."""
    settings = load_settings()
    cfg = settings["research"]
    target = find_target(firm, load_targets())

    log.info("Researching %s (%s)", target["firm"], target["domain"])
    fetcher = Fetcher(cfg)
    crawled, texts, blocking = crawl_firm(fetcher, target, cfg, refresh)

    artifact = build_artifact(target, crawled, texts, cfg, blocking)
    validate_artifact(artifact)

    out_dir = resolve_path(cfg["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{artifact['firm_slug']}.json"
    out_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")

    pages_ok = sum(1 for p in crawled if not p.get("error"))
    log.info("  %s pages read, %s hooks, confidence=%s -> %s",
             pages_ok, len(artifact["alignment_hooks"]), artifact["confidence"],
             out_path.relative_to(CFG_ROOT))

    # Rule 1: nothing citable means no draft, ever. The manual queue is the
    # correct destination, not a generic paragraph.
    if not artifact["alignment_hooks"] or artifact["confidence"] == "low":
        queue_path = route_to_manual_queue(artifact, settings)
        log.warning("  Routed to manual queue (%s). No draft will be generated.",
                    queue_path.name)

    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--firm", help="Firm name as it appears in data/targets.csv")
    group.add_argument("--all", action="store_true", help="Research every target")
    parser.add_argument("--owner", help="With --all, restrict to one owner")
    parser.add_argument("--refresh", action="store_true",
                        help="Bypass the HTML cache and re-crawl")
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
            research_firm(name, refresh=args.refresh)
        except Exception as exc:
            failures += 1
            log.error("Research failed for %s: %s: %s", name, type(exc).__name__, exc)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
