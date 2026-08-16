"""Screenshot a running Bridge page for visual review.

Always points at a real server. A file:// render misses the CSP, the served
asset versions, and the API calls that fill the page, so it cannot tell you
whether what shipped actually looks right.

Uses the system Chrome via Playwright's `channel` rather than a downloaded
Chromium build, which keeps this working without a browser download step.

Usage:
    python tools/screenshot.py http://localhost:8000
    python tools/screenshot.py http://localhost:8000 intake --width 1440
    python tools/screenshot.py http://localhost:8000 intake-mobile --width 390

Saves to review/checkpoints/shot-<n>[-label].png, auto-incremented so an earlier
comparison round is never overwritten.
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.logging import get_logger  # noqa: E402

log = get_logger("tools.screenshot")

OUT_DIR = PROJECT_ROOT / "review" / "checkpoints"

# Tried in order. The first that launches wins, so a machine with only Edge or
# only Chromium still works.
CHANNELS = ("chrome", "msedge", None)


def next_path(label: str = "") -> Path:
    """Never overwrite an earlier round."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index = 1 + len(list(OUT_DIR.glob("shot-*.png")))
    suffix = f"-{label}" if label else ""
    while (path := OUT_DIR / f"shot-{index}{suffix}.png").exists():
        index += 1
    return path


# Reveals the signed-in shell without credentials, for visual review only. It
# touches nothing but display state and sample text in the DOM, so what it
# photographs is the real markup under the real stylesheet.
PREVIEW_INTAKE_JS = """
() => {
  document.querySelector('#login-view').hidden = true;
  document.querySelector('#login-view').classList.add('hidden');
  const app = document.querySelector('#app-view');
  app.hidden = false;
  app.classList.remove('hidden');
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelector('#view-pipeline').classList.add('active');
  document.querySelector('#page-title').textContent = 'Firm pipeline';
  document.querySelector('#user-name').textContent = 'Jamari Myers';
  document.querySelector('#user-email').textContent = 'jamari@blkcapitalmanagement.org';
  document.querySelector('#owner-badge').textContent = 'jamari lane';
  document.querySelector('#sync-status').textContent = 'Preview';

  const select = document.querySelector('#intake-category');
  select.innerHTML = ['Not set','Growth Equity','PE','PE (Middle Market)','Private Credit',
    'Private Markets','Multi-Strat HF','Options HF','Quant HF','Asset Management','IB',
    'Real Estate','Venture Capital','Endowment','Fund of Funds']
    .map(l => `<option>${l}</option>`).join('');
  select.selectedIndex = 2;

  document.querySelector('#intake-firm').value = 'General Atlantic';
  document.querySelector('#intake-website').value = 'https://www.generalatlantic.com';
  document.querySelector('#intake-domain').value = 'generalatlantic.com';
  document.querySelector('#intake-linkedin').value =
    'https://www.linkedin.com/company/general-atlantic';
  document.querySelector('#intake-email-format').value = '{f}{last}@generalatlantic.com';
  document.querySelector('#intake-email-source').value =
    'https://www.generalatlantic.com/news/press-release';
  document.querySelector('#intake-email-format').dispatchEvent(
    new Event('input', {bubbles: true}));

  const results = document.querySelector('#intake-results');
  results.hidden = false;
  results.innerHTML = `
    <div class="intake-result"><b>Alpha Partners</b><span>Added to your lane.</span></div>
    <div class="intake-result is-warned"><b>Meridian</b><span>Looks similar to existing target 'Meridian Intl'. Added anyway.</span></div>
    <div class="intake-result is-skipped"><b>Known Capital</b><span>Already in your pipeline.</span></div>`;
}
"""

PREVIEW_BATCH_JS = PREVIEW_INTAKE_JS.rstrip()[:-1] + """
  document.querySelector('#intake-results').hidden = true;
  document.querySelector('#selection-count').textContent = '30 selected';
  document.querySelector('#batch-hint').textContent =
    '30 firm(s) selected. Research runs in batches of 5 and takes roughly 6 minute(s) for this many. You can watch it below.';
  document.querySelector('#batch-hint').classList.add('is-ready');
  ['#derive-selected','#research-selected','#contacts-selected']
    .forEach(s => document.querySelector(s).disabled = false);

  const panel = document.querySelector('#batch-progress');
  panel.hidden = false;
  document.querySelector('#batch-progress-title').textContent = 'Researching firms';
  document.querySelector('#batch-progress-detail').textContent =
    "Crawling each firm's own site at one request per second. This takes a while.";
  document.querySelector('#batch-progress-count').textContent = '10 of 30';
  document.querySelector('#batch-bar-fill').style.width = '33%';
  document.querySelector('#batch-progress-log').innerHTML = `
    <div class="batch-log-row"><b>Ares Management</b><span>research medium, 2 hook(s)</span></div>
    <div class="batch-log-row"><b>Citadel</b><span>research high, 3 hook(s)</span></div>
    <div class="batch-log-row is-failed"><b>Audax Group</b><span>research confidence is low</span></div>
    <div class="batch-log-row"><b>Adams Street Partners</b><span>research high, 3 hook(s)</span></div>`;
}
"""

PREVIEWS = {"intake": PREVIEW_INTAKE_JS, "batch": PREVIEW_BATCH_JS}


def capture(url: str, label: str = "", width: int = 1440, height: int = 900,
            full_page: bool = True, wait_ms: int = 1200,
            preview: str = "") -> Path:
    """Render one page and write a PNG. Returns the path written."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise SystemExit(
            "playwright is not installed. Run: pip install playwright"
        ) from exc

    out = next_path(label)
    console: list[str] = []

    with sync_playwright() as p:
        browser = None
        for channel in CHANNELS:
            try:
                browser = p.chromium.launch(channel=channel) if channel \
                    else p.chromium.launch()
                break
            except Exception as exc:
                log.debug("Could not launch %s: %s", channel or "bundled chromium", exc)
        if browser is None:
            raise SystemExit(
                "No usable browser. Install Chrome, or run: playwright install chromium"
            )

        page = browser.new_page(viewport={"width": width, "height": height})
        # A CSP violation is a real defect on this page, so surface it rather
        # than letting a silently blocked stylesheet look like a design mistake.
        page.on("console", lambda msg: console.append(f"{msg.type}: {msg.text}"))
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(wait_ms)
        if preview:
            if preview not in PREVIEWS:
                raise SystemExit(f"Unknown preview {preview!r}. "
                                 f"Known: {', '.join(sorted(PREVIEWS))}")
            page.evaluate(PREVIEWS[preview])
            page.wait_for_timeout(250)
        page.screenshot(path=str(out), full_page=full_page)
        browser.close()

    log.info("Wrote %s", out)
    for line in console:
        if "content security policy" in line.lower() or "refused" in line.lower():
            log.warning("CSP: %s", line)
        elif line.startswith("error"):
            log.warning("Console %s", line)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", help="Page to capture, e.g. http://localhost:8000")
    parser.add_argument("label", nargs="?", default="", help="Suffix for the filename")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--viewport-only", action="store_true",
                        help="Capture the viewport instead of the full page")
    parser.add_argument("--preview", default="", choices=["", *sorted(PREVIEWS)],
                        help="Reveal a signed-in view for visual review without credentials")
    args = parser.parse_args()

    if args.url.startswith("file://"):
        parser.error("Screenshot a running server, not a file:// URL.")

    path = capture(args.url, args.label, args.width, args.height,
                   full_page=not args.viewport_only, preview=args.preview)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
