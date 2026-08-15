"""HTML to evidence. Pure text processing, no network access.

Kept separate from fetch.py so extraction is testable against fixture HTML with
no crawling involved.

Every extracted item is a sentence taken verbatim from the firm's own page,
paired with the URL it came from. Nothing is paraphrased here. Paraphrasing is
what turns a citation into a claim nobody can check.
"""

import re
from typing import Any

from bs4 import BeautifulSoup

# Page categories the crawler looks for. Four is the ceiling by design: deeper
# crawling produces paragraphs that read as research for its own sake.
CATEGORY_LINK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "university_recruiting": (
        "campus", "campus-recruiting", "university", "university-relations",
        "students", "early-careers", "early-career", "undergraduate",
        "internship", "internships", "summer-analyst", "analyst-program",
        "emerging-talent", "graduate-program", "students-and-graduates",
    ),
    "careers": (
        "careers", "career", "jobs", "join-us", "work-with-us", "working-at",
        "life-at", "recruiting", "career-opportunities", "job-opportunities",
        "our-teams",
    ),
    "values_culture": (
        "values", "culture", "our-culture", "values-and-culture", "about-us",
        "who-we-are", "our-people", "our-firm", "mission", "diversity",
        "inclusion", "responsibility", "citizenship", "about",
    ),
    "news": (
        "news", "newsroom", "press", "press-releases", "press-release",
        "media", "announcements", "in-the-news",
    ),
}

# Keywords that mean a page is about investing, not about the firm's people.
# "opportunities" and "insights" match investment-strategy and thought-leadership
# pages constantly, which is how a careers slot ends up holding a deal page.
CATEGORY_NEGATIVE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "careers": ("investment", "strategy", "portfolio", "ideas", "insights",
                "global-opportunities", "investor", "funds"),
    "university_recruiting": ("investment", "strategy", "portfolio", "ideas",
                              "insights", "investor"),
    "values_culture": ("ideas", "insights", "article", "blog", "portfolio",
                       "investment-strategy", "news"),
    "news": ("careers", "jobs", "investment-strategy"),
}

# Sentence-level patterns per artifact field.
FIELD_PATTERNS: dict[str, tuple[str, ...]] = {
    "values_themes": (
        r"\bvalues?\b", r"\bculture\b", r"\bwe believe\b", r"\bmission\b",
        r"\bcollaborat", r"\bintegrity\b", r"\bcuriosity\b", r"\bmeritocra",
        r"\bdiversity\b", r"\binclusi", r"\bour people\b", r"\bprincipl",
        r"\bentrepreneurial\b", r"\bexcellence\b",
    ),
    "campus_or_early_careers_programs": (
        r"\bintern(ship)?s?\b", r"\bcampus\b", r"\buniversit", r"\bundergraduate",
        r"\bearly[- ]career", r"\banalyst program", r"\bsummer analyst",
        r"\bstudents?\b", r"\brotational\b", r"\bfellowship\b", r"\bscholars?\b",
        r"\bapprentice", r"\bfirst[- ]year", r"\bsophomore", r"\bjunior",
    ),
    "recruiting_timeline_signals": (
        r"\bapplications? (are |will )?open", r"\bapply by\b", r"\bdeadline\b",
        r"\brecruiting (season|cycle|timeline)", r"\bclass of \d{4}",
        r"\brolling basis\b", r"\bapplications? for\b", r"\bopens in\b",
        r"\b(spring|summer|fall|autumn|winter) 20\d{2}\b", r"\bcohort\b",
    ),
    "offices_and_regions": (
        r"\boffices?\b", r"\bheadquarter", r"\bbased in\b", r"\blocations?\b",
        r"\bpresence in\b", r"\boperates in\b", r"\bglobal(ly)?\b",
    ),
    "asset_classes": (
        r"\bprivate equity\b", r"\bprivate credit\b", r"\bdirect lending\b",
        r"\bgrowth equity\b", r"\breal estate\b", r"\binfrastructure\b",
        r"\bhedge fund\b", r"\bmulti[- ]strateg", r"\bequities\b",
        r"\bfixed income\b", r"\bventure\b", r"\basset management\b",
        r"\bcredit\b", r"\bbuyout", r"\bsecondaries\b", r"\bopportunistic\b",
    ),
    "existing_student_org_partnerships": (
        r"\bpartner(ship)?s? with\b", r"\bstudent organi", r"\bsponsors?\b",
        r"\bSEO\b", r"\bManagement Leadership for Tomorrow\b", r"\bMLT\b",
        r"\bGirls Who Invest\b", r"\bJumpstart\b", r"\bToigo\b",
        r"\bcampus partner", r"\bnonprofit partner", r"\baffinity group",
        r"\bscholarship\b", r"\bpipeline program\b",
    ),
    # Announcement verbs only. Bare "open" and "expand" matched careers-page
    # boilerplate like "If you do not see a position open...", which is not news.
    "recent_relevant_news": (
        r"\bannounced\b", r"\bhas (closed|launched|raised|appointed|named)\b",
        r"\bcompleted the\b", r"\bacquired\b", r"\bappointed\b",
        r"\bhas been named\b", r"\blaunched\b", r"\braised \$", r"\bclosed \$",
        r"\bexpanded (its|our|into)\b", r"\bopened (its|our|a new)\b",
    ),
}

# Some fields only mean what their name says when a second signal is present.
# "partnership with" matches investment deals far more often than student
# organizations, and a deal sentence dressed up as a campus partnership is
# exactly the kind of false claim that must never reach a sponsor.
FIELD_REQUIRED_CONTEXT: dict[str, str] = {
    "existing_student_org_partnerships": (
        r"\bstudent|\bcampus|\buniversit|\bundergraduate|\bschool|\bcollege"
        r"|\bscholarship|\bnonprofit|\bnon-profit|\bearly[- ]career|\bintern"
        r"|\bMLT\b|\bSEO\b|\bGirls Who Invest\b|\bToigo\b|\bJumpstart\b"
    ),
    "recruiting_timeline_signals": (
        r"\bappl|\brecruit|\bhiring\b|\bintern|\bprogram|\bcohort|\bclass of\b"
    ),
    "campus_or_early_careers_programs": (
        r"\bprogram|\bintern|\brecruit|\bhiring\b|\bapplicat|\bopportunit"
        r"|\bexperience|\btrain|\bdevelop|\bjoin\b|\bcareer|\bstudent"
        r"|\banalyst\b|\bcohort|\bscholar"
    ),
}

# Boilerplate that is never evidence about a firm.
_JUNK_PATTERNS = (
    r"cookie", r"privacy polic", r"terms of use", r"all rights reserved",
    r"javascript", r"enable js", r"©", r"subscribe to our", r"newsletter",
    r"click here", r"skip to (main )?content", r"@media", r"function\s*\(",
    r"^\s*(home|menu|search|close|back|next|previous)\s*$",
    r"this (site|website) uses", r"opt[- ]out", r"gdpr",
    # UI chrome glued into prose by get_text(). A sentence containing these is a
    # concatenation of unrelated page fragments, not something the firm wrote.
    r"\bread more\b", r"\blearn more\b", r"\bview all\b", r"\bsee more\b",
    r"\bshow more\b", r"\bexplore more\b", r"\bfind out more\b",
    r"\bdownload\b", r"\bsign up\b", r"\blog in\b", r"\bcontact us\b",
    # Carousel counters ("3 / 25") and slide labels.
    r"\b\d+\s*/\s*\d+\b",
    # Securities disclaimers. Every asset manager's site carries them, they match
    # value and risk vocabulary, and they say nothing about the firm.
    r"\bspeculative\b", r"high degree of risk", r"\brisk of loss\b",
    r"past performance", r"no assurance", r"not an offer", r"\bsolicitation\b",
    r"forward[- ]looking statements", r"\bprospectus\b", r"may lose value",
    r"\bindicative of future\b", r"consult (your|a) (financial|tax|legal)",
)
_JUNK = re.compile("|".join(_JUNK_PATTERNS), re.IGNORECASE)

# Two or more consecutive ALL-CAPS words is a section heading that got glued to
# the following sentence, e.g. "SELECT INVESTMENTS In 2020, ...".
_GLUED_HEADING = re.compile(r"\b[A-Z]{3,}(?:\s+[A-Z]{3,})+\b")

# Leading UI words that survive stripping, e.g. "Expand Expand and increase...".
_LEADING_CHROME = re.compile(
    r"^(?:read more|learn more|expand|collapse|view|explore|share|watch|play|"
    r"menu|search|close|back|next|previous|more)\b[\s>|\-–:]*",
    re.IGNORECASE,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")

MIN_SENTENCE_CHARS = 45
MAX_SENTENCE_CHARS = 320


def page_text(html: str) -> str:
    """Strip an HTML document to readable body text, one block per line.

    Block boundaries matter more than they look. Flattening the whole document
    with a space separator glues headings onto the paragraph that follows them,
    producing "sentences" like "Internships | Balyasny Asset Management
    internships High impact hands-on internships We hire interns for...". Those
    read as garbage in an evidence block. Newlines here become hard sentence
    boundaries in `sentences()`.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header",
                     "form", "svg", "iframe", "title"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse runs of spaces within a line, but keep line structure.
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def page_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Return (absolute_url, anchor_text) pairs for same-document links."""
    from urllib.parse import urljoin

    soup = BeautifulSoup(html, "lxml")
    links: list[tuple[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        links.append((urljoin(base_url, href), anchor.get_text(" ", strip=True)))
    return links


def score_link(
    url: str,
    anchor_text: str,
    keywords: tuple[str, ...],
    negative_keywords: tuple[str, ...] = (),
) -> int:
    """Score a link's fit for a category. Higher is better, 0 means no match.

    Matching is on whole path segments and whole anchor words, not raw substrings.
    Substring matching is what let "opportunities" pull an investment-strategy
    page into the careers slot.
    """
    from urllib.parse import urlparse

    path = urlparse(url).path.lower()
    segments = [s for s in re.split(r"[/_.]", path) if s]
    anchor = anchor_text.lower()
    anchor_words = set(re.split(r"[^a-z]+", anchor)) - {""}

    score = 0
    for keyword in keywords:
        keyword_words = set(re.split(r"[^a-z]+", keyword)) - {""}
        # A keyword in the anchor text is a stronger signal than one buried in a
        # URL slug that might match incidentally.
        if keyword_words and keyword_words <= anchor_words:
            score += 4
        if keyword in segments:
            score += 3
        elif any(seg == keyword or seg.startswith(keyword + "-") for seg in segments):
            score += 2

    if score == 0:
        return 0

    for negative in negative_keywords:
        if negative in segments or negative in anchor_words:
            score -= 5

    # Prefer shallow URLs: /careers beats /about/foo/bar/careers-old.
    depth = len(segments)
    return max(0, score - max(0, depth - 2))


def sentences(text: str) -> list[str]:
    """Split page text into candidate evidence sentences, dropping boilerplate.

    Anything that survives here is quoted verbatim into an artifact and may end
    up in front of a sponsor, so this filter errs toward dropping good sentences
    rather than keeping fragments of navigation.
    """
    out: list[str] = []
    candidates: list[str] = []
    # Each block is its own sentence universe, so a heading cannot merge with the
    # paragraph beneath it.
    for block in text.splitlines():
        candidates.extend(_SENTENCE_SPLIT.split(block))

    for raw in candidates:
        sentence = _LEADING_CHROME.sub("", raw.strip()).strip()

        # A second pass catches doubled chrome ("Expand Expand and increase...").
        sentence = _LEADING_CHROME.sub("", sentence).strip()

        if not (MIN_SENTENCE_CHARS <= len(sentence) <= MAX_SENTENCE_CHARS):
            continue
        if _JUNK.search(sentence) or _GLUED_HEADING.search(sentence):
            continue
        # Real prose has spaces and at least one lowercase run. Nav dumps do not.
        if sentence.count(" ") < 6 or not re.search(r"[a-z]{4}", sentence):
            continue
        # Must read as a sentence, not a list of links jammed together.
        if not sentence[0].isupper() or not sentence.rstrip().endswith((".", "!", "?")):
            continue
        out.append(sentence)
    return out


def extract_fields(
    text: str, source_url: str, category: str, max_per_field: int = 3
) -> dict[str, list[dict[str, Any]]]:
    """Match page sentences against each field's patterns.

    Returns {field_name: [evidence, ...]}. Every evidence item quotes the page
    verbatim and carries source_url, so nothing downstream has to trust this
    module's judgment about what the sentence means.
    """
    found: dict[str, list[dict[str, Any]]] = {name: [] for name in FIELD_PATTERNS}
    candidates = sentences(text)

    for field_name, patterns in FIELD_PATTERNS.items():
        compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
        required = FIELD_REQUIRED_CONTEXT.get(field_name)
        required_re = re.compile(required, re.IGNORECASE) if required else None
        seen: set[str] = set()
        for sentence in candidates:
            if len(found[field_name]) >= max_per_field:
                break
            hits = sum(1 for pattern in compiled if pattern.search(sentence))
            if hits == 0:
                continue
            # Fields with a required context need a second, independent signal.
            if required_re and not required_re.search(sentence):
                continue
            key = sentence.lower()[:80]
            if key in seen:
                continue
            seen.add(key)
            found[field_name].append({
                "value": sentence,
                "source_url": source_url,
                "quote": sentence,
                "category": category,
            })
    return found
