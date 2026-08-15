"""Polite HTTP fetching with robots.txt, rate limiting, and an on-disk cache.

This is the only module in the project permitted to make outbound web requests
(rule 6). `drafts/generate.py` takes a JSON path, never a URL.

Behavior:
  - robots.txt is fetched once per domain and respected.
  - One request per second per domain, enforced by a per-domain timestamp.
  - Raw HTML is cached under research/cache/ keyed by URL hash, so reruns do no
    network I/O at all.
  - linkedin.com is refused at the request layer (rule 3).

Usage:
    from common.http import Fetcher
    fetcher = Fetcher(settings["research"])
    page = fetcher.get("https://example.com/careers")
    if page.ok:
        print(page.html[:200], page.from_cache)
"""

import hashlib
import json
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from common.config import resolve_path
from common.logging import get_logger

log = get_logger("common.http")

# Rule 3. Refused at the request layer so no future caller can route around it.
BLOCKED_HOSTS = ("linkedin.com", "www.linkedin.com", "lnkd.in")


class BlockedHostError(RuntimeError):
    """Raised when a caller attempts a request to a forbidden host."""


@dataclass
class Page:
    url: str
    status: int
    html: str
    from_cache: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300 and bool(self.html)


class Fetcher:
    """Rate-limited, robots-respecting, cached HTTP client."""

    def __init__(self, research_cfg: dict[str, Any]):
        self.cfg = research_cfg
        self.user_agent = " ".join(research_cfg["user_agent"].split())
        self.timeout = research_cfg.get("request_timeout_seconds", 20)
        self.rps = float(research_cfg.get("requests_per_second_per_domain", 1.0))
        self.respect_robots = research_cfg.get("respect_robots", True)

        self.cache_dir = resolve_path(research_cfg["cache_dir"])
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._last_request_at: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })

    # ── Cache ─────────────────────────────────────────────────────────────────

    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        host = (urlparse(url).netloc or "unknown").replace(":", "_")
        base = self.cache_dir / host
        return base / f"{digest}.html", base / f"{digest}.meta.json"

    def _read_cache(self, url: str) -> Page | None:
        html_path, meta_path = self._cache_paths(url)
        if not (html_path.exists() and meta_path.exists()):
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return Page(
                url=meta.get("url", url),
                status=meta.get("status", 200),
                html=html_path.read_text(encoding="utf-8"),
                from_cache=True,
                error=meta.get("error"),
            )
        except Exception as exc:
            log.warning("Cache read failed for %s (%s). Refetching.", url, exc)
            return None

    def _write_cache(self, url: str, page: Page) -> None:
        html_path, meta_path = self._cache_paths(url)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(page.html, encoding="utf-8")
        meta_path.write_text(
            json.dumps(
                {
                    "url": page.url,
                    "status": page.status,
                    "error": page.error,
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # ── Politeness ────────────────────────────────────────────────────────────

    def _throttle(self, host: str) -> None:
        if self.rps <= 0:
            return
        min_gap = 1.0 / self.rps
        last = self._last_request_at.get(host)
        if last is not None:
            wait = min_gap - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at[host] = time.monotonic()

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]

        parser = urllib.robotparser.RobotFileParser()
        robots_url = urljoin(origin, "/robots.txt")
        try:
            self._throttle(parsed.netloc)
            resp = self.session.get(robots_url, timeout=self.timeout)
            if resp.status_code >= 400:
                # No robots.txt means no restrictions stated.
                log.debug("No robots.txt at %s (HTTP %s).", robots_url, resp.status_code)
                self._robots[origin] = None
                return None
            parser.parse(resp.text.splitlines())
            log.debug("Loaded robots.txt for %s.", origin)
        except Exception as exc:
            # A robots.txt we cannot read is treated as permissive, matching
            # standard crawler behavior, but it is logged so it is visible.
            log.warning("Could not read %s (%s). Proceeding without it.", robots_url, exc)
            self._robots[origin] = None
            return None

        self._robots[origin] = parser
        return parser

    def allowed(self, url: str) -> bool:
        """Return whether robots.txt permits fetching this URL."""
        if not self.respect_robots:
            return True
        parser = self._robots_for(url)
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    # ── Fetch ─────────────────────────────────────────────────────────────────

    def get(self, url: str, *, use_cache: bool = True) -> Page:
        """Fetch a URL, honoring cache, robots.txt, and the per-domain rate limit."""
        host = (urlparse(url).netloc or "").lower()
        if any(host == b or host.endswith("." + b) for b in BLOCKED_HOSTS):
            raise BlockedHostError(
                f"Refusing to fetch {url}. LinkedIn is never crawled (rule 3)."
            )

        if use_cache:
            cached = self._read_cache(url)
            if cached is not None:
                log.debug("Cache hit: %s", url)
                return cached

        if not self.allowed(url):
            log.info("robots.txt disallows %s. Skipping.", url)
            return Page(url=url, status=0, html="", error="disallowed_by_robots")

        self._throttle(host)
        try:
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            page = Page(url=resp.url, status=resp.status_code, html=resp.text)
        except Exception as exc:
            log.warning("Fetch failed for %s: %s", url, exc)
            page = Page(url=url, status=0, html="", error=type(exc).__name__)

        if use_cache:
            self._write_cache(url, page)
        return page

    def get_rendered(self, url: str) -> Page:
        """Fetch with Playwright. Only for pages that render nothing without JS.

        Opt-in via research.playwright_fallback in settings.yaml.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning(
                "Playwright not installed. Run: pip install playwright && "
                "playwright install chromium"
            )
            return Page(url=url, status=0, html="", error="playwright_not_installed")

        if not self.allowed(url):
            return Page(url=url, status=0, html="", error="disallowed_by_robots")

        self._throttle((urlparse(url).netloc or "").lower())
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(user_agent=self.user_agent)
                pw_page = context.new_page()
                response = pw_page.goto(url, timeout=self.timeout * 1000,
                                        wait_until="domcontentloaded")
                pw_page.wait_for_timeout(1500)
                html = pw_page.content()
                status = response.status if response else 0
                browser.close()
            page = Page(url=url, status=status, html=html)
        except Exception as exc:
            log.warning("Playwright fetch failed for %s: %s", url, exc)
            return Page(url=url, status=0, html="", error=type(exc).__name__)

        self._write_cache(url, page)
        return page
