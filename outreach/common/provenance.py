"""Provenance and style enforcement for generated copy.

This module is where rules 1, 4, and 5 are actually enforced. Nothing generated
reaches a draft file without passing through `check_firm_paragraph`.

The core idea: the firm paragraph may only assert things that already appear in
the research artifact's `alignment_hooks`, each of which carries a
`firm_claim_source` (the firm namespace; see common/namespaces.py).
Any capitalized name, number, or firm-specific term in the paragraph that cannot
be found in a hook (or in the approved BLK vocabulary from blk_facts.json) is an
invented fact, and generation fails.

Usage:
    from common.provenance import check_firm_paragraph, ProvenanceError
    report = check_firm_paragraph(paragraph, hooks, facts)
    if not report.ok:
        raise ProvenanceError(report.describe())
"""

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from common.logging import get_logger
from common.namespaces import firm_claim_source_of

log = get_logger("common.provenance")

# U+2014 em dash and its lookalikes. Rule 4, no exceptions.
EM_DASH_CHARS = "—–―⸺⸻"
_ASCII_EM_DASH = re.compile(r"\s--\s|\s--$")

# Words that read as flattery or eagerness. The house tone forbids both.
BANNED_TONE_WORDS = frozenset({
    "amazing", "awesome", "esteemed", "excited", "exciting", "extraordinary",
    "fantastic", "honored", "impressive", "incredible", "legendary", "premier",
    "prestigious", "renowned", "remarkable", "storied", "thrilled", "unparalleled",
    "world-class", "phenomenal", "eager", "delighted", "privileged",
})

# Tokens that are capitalized but assert nothing about the firm.
_ENTITY_STOPWORDS = frozenset({
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "i", "if", "in",
    "is", "it", "its", "of", "on", "or", "our", "that", "the", "their", "them",
    "these", "they", "this", "to", "we", "what", "when", "where", "which",
    "while", "who", "with", "your", "you",
})

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_WORD = re.compile(r"[A-Za-z][A-Za-z'&.\-]*|\d[\d,.%]*")


@dataclass
class ClaimDetection:
    """One lexical claim span and the detector rule that identified it."""
    phrase: str
    claim_type: str


@dataclass
class SentenceEvidence:
    """One paragraph sentence and the selected hook(s) that back it."""
    sentence: str
    source_url: str | None
    hook_index: int | None
    unsupported_terms: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    hook_indices: list[int] = field(default_factory=list)
    hook_ids: list[str] = field(default_factory=list)
    unsupported_phrase: str | None = None
    unsupported_claims: list[ClaimDetection] = field(default_factory=list)

    @property
    def supported(self) -> bool:
        return (
            bool(self.source_url or self.source_urls)
            and not self.unsupported_terms
            and not self.unsupported_claims
        )


@dataclass
class ProvenanceReport:
    violations: list[str] = field(default_factory=list)
    sentences: list[SentenceEvidence] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def describe(self) -> str:
        lines = [f"  - {v}" for v in self.violations]
        return "Generated copy failed provenance checks:\n" + "\n".join(lines)


class ProvenanceError(RuntimeError):
    """Raised when generated copy asserts something with no source_url behind it."""


# ── Style checks ──────────────────────────────────────────────────────────────

def assert_no_em_dash(text: str, where: str = "generated copy") -> None:
    """Raise ProvenanceError on any em dash. Rule 4."""
    for idx, ch in enumerate(text):
        if ch in EM_DASH_CHARS:
            start = max(0, idx - 40)
            raise ProvenanceError(
                f"Em dash (U+{ord(ch):04X}) found in {where} at position {idx}: "
                f"...{text[start:idx + 40]!r}... "
                "House style forbids em dashes with no exceptions (rule 4)."
            )
    match = _ASCII_EM_DASH.search(text)
    if match:
        raise ProvenanceError(
            f"ASCII em dash substitute '--' found in {where} at position "
            f"{match.start()}. Rewrite the sentence (rule 4)."
        )


def find_tone_violations(text: str) -> list[str]:
    """Return banned flattery and eagerness words present in the text."""
    words = {w.lower().strip(".,;:'\"") for w in text.split()}
    return sorted(words & BANNED_TONE_WORDS)


# ── Provenance ────────────────────────────────────────────────────────────────

def split_sentences(paragraph: str) -> list[str]:
    """Split a short paragraph into sentences."""
    text = " ".join(paragraph.split())
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _claim_terms(sentence: str) -> list[ClaimDetection]:
    """Return checkable proper-noun and number spans with detector metadata.

    The first token of a sentence is capitalized by grammar, not by meaning, so
    it only counts when it is not an ordinary word.
    """
    tokens = _WORD.findall(sentence)
    terms: list[ClaimDetection] = []
    for position, token in enumerate(tokens):
        lowered = token.lower().strip(".")
        if lowered in _ENTITY_STOPWORDS:
            continue
        is_number = any(ch.isdigit() for ch in token)
        is_proper = token[0].isupper()
        if position == 0 and is_proper and not is_number and lowered.isalpha():
            # Sentence-initial capitalization is not evidence of a proper noun.
            # It still gets checked if it appears capitalized mid-sentence.
            if token not in " ".join(tokens[1:]):
                continue
        if is_number or is_proper:
            terms.append(ClaimDetection(
                phrase=token.strip("."),
                claim_type="number" if is_number else "proper_noun",
            ))
    return terms


def _vocabulary(values: Iterable[Any]) -> set[str]:
    """Flatten arbitrary config values into a lowercase token set."""
    vocab: set[str] = set()
    stack = list(values)
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
        elif item is not None:
            for token in _WORD.findall(str(item)):
                vocab.add(token.lower().strip("."))
    return vocab


def _approved_blk_vocabulary(facts: dict[str, Any]) -> set[str]:
    vocab = _vocabulary(facts.values())
    # Terms BLK may always use about itself, independent of the stats file.
    vocab |= {
        "blk", "blk's", "capital", "management", "us", "u.s.", "emea", "uk",
        "members", "member", "undergraduate", "students", "campus", "fall",
        "conference", "new", "york", "wells", "fargo", "i", "we", "our",
    }
    return vocab


_CONTENT_STOPWORDS = _ENTITY_STOPWORDS | frozenset({
    "also", "already", "can", "could", "day", "does", "firm", "firm's",
    "group", "has", "have", "having", "makes", "make", "may", "people",
    "program", "programs", "same", "something", "team", "teams", "through",
    "will", "would",
})

# These words connect a sourced firm fact to BLK's mission.  They express the
# writer's interpretation rather than a new factual assertion about the firm.
_INTERPRETIVE_VOCAB = frozenset({
    "ambitious", "connect", "connecting", "connection", "drawn", "emphasis",
    "fit", "focus", "natural", "network", "partner", "partnership", "relevant",
    "relationship", "student", "students", "talent", "undergraduate",
    "undergraduates",
})

_FIRM_REFERENCE = re.compile(
    r"\b(?:the firm|the company|its|their|they|them|your|you)\b", re.IGNORECASE
)

# A small, deliberately conservative set of claim patterns that are dangerous
# when they appear in human-authored copy without matching language in a hook.
_FACTUAL_CLAIM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bmore than\b", re.IGNORECASE), "more than"),
    (re.compile(r"\bless than\b", re.IGNORECASE), "less than"),
    (re.compile(r"\bany competing\b", re.IGNORECASE), "any competing"),
    (re.compile(r"\bnext year\b", re.IGNORECASE), "next year"),
    (re.compile(r"\b(?:double|triple)[sd]?\b", re.IGNORECASE), "double/triple"),
    (re.compile(r"\bplans?\b|\bplanned\b", re.IGNORECASE), "plans"),
    (re.compile(r"\b(?:hire|hires|hired|hiring)\b", re.IGNORECASE), "hires"),
    # Include a hyphenated modifier in the matched lexical span.  A selected
    # hook that contains "category-leading" supports that exact phrase, while
    # it does not automatically support a standalone claim that a firm is
    # "leading".
    (
        re.compile(
            r"\b(?:[A-Za-z]+-)?(?:largest|leading|only|most)\b",
            re.IGNORECASE,
        ),
        "superlative",
    ),
    (re.compile(r"\bspecifically identified\b", re.IGNORECASE), "specifically identified"),
    (re.compile(r"\brecruiting priority\b", re.IGNORECASE), "recruiting priority"),
)


def _normal_token(token: str) -> str:
    """Normalize just enough morphology to compare copy with source prose."""
    value = token.lower().replace("’", "'").strip(".'")
    if value.endswith("'s"):
        value = value[:-2]
    if value in {"u.s", "u.s.", "us"}:
        return "us"
    if len(value) > 5 and value.endswith("ies"):
        return value[:-3] + "y"
    if len(value) > 4 and value.endswith("es"):
        return value[:-1]
    if len(value) > 3 and value.endswith("s") and not value.endswith("ss"):
        return value[:-1]
    return value


def _normalized_vocabulary(value: str) -> set[str]:
    return {_normal_token(token) for token in _WORD.findall(value) if _normal_token(token)}


def _phrase_is_sourced(phrase: str, hook_vocabs: list[set[str]]) -> bool:
    phrase_tokens = _normalized_vocabulary(phrase)
    return bool(phrase_tokens) and any(phrase_tokens <= vocab for vocab in hook_vocabs)


def _unsupported_pattern_claims(
    sentence: str,
    hook_vocabs: list[set[str]],
) -> list[ClaimDetection]:
    """Return unsourced matched spans, never detector category labels."""
    claims: list[ClaimDetection] = []
    for pattern, claim_type in _FACTUAL_CLAIM_PATTERNS:
        for match in pattern.finditer(sentence):
            phrase = match.group(0)
            if not _phrase_is_sourced(phrase, hook_vocabs):
                claims.append(ClaimDetection(
                    phrase=phrase,
                    claim_type=claim_type,
                ))
    return claims


def _append_unique_claim(
    claims: list[ClaimDetection],
    claim: ClaimDetection,
) -> None:
    """Append a claim unless its actual lexical span is already present."""
    normalized = claim.phrase.casefold()
    if all(existing.phrase.casefold() != normalized for existing in claims):
        claims.append(claim)


def check_firm_paragraph(
    paragraph: str,
    hooks: list[dict[str, Any]],
    facts: dict[str, Any],
    *,
    firm_name: str = "",
    min_sentences: int = 2,
    max_sentences: int = 3,
) -> ProvenanceReport:
    """Validate a generated firm paragraph against its research hooks.

    Args:
        paragraph: The generated [FIRM_PARAGRAPH] prose.
        hooks: alignment_hooks from the research artifact. Each must have `text`
            (or `value`) and `source_url`.
        facts: Parsed blk_facts.json, the approved BLK vocabulary.

    Returns:
        A ProvenanceReport. `.ok` is False if anything is unsupported.
    """
    report = ProvenanceReport()

    if not paragraph or not paragraph.strip():
        report.violations.append("firm paragraph is empty")
        return report

    usable_hooks = [h for h in hooks if firm_claim_source_of(h)]
    if not usable_hooks:
        report.violations.append(
            "no alignment_hooks carry a source_url, so no firm-specific claim can "
            "be made (rule 1)"
        )
        return report

    # Style gates first. These are absolute.
    try:
        assert_no_em_dash(paragraph, "firm paragraph")
    except ProvenanceError as exc:
        report.violations.append(str(exc))

    tone_hits = find_tone_violations(paragraph)
    if tone_hits:
        report.violations.append(
            f"flattery or eagerness words present: {', '.join(tone_hits)}. "
            "House tone forbids both."
        )

    sentences = split_sentences(paragraph)
    if not (min_sentences <= len(sentences) <= max_sentences):
        report.violations.append(
            f"firm paragraph has {len(sentences)} sentence(s); "
            f"required {min_sentences} to {max_sentences}"
        )

    hook_vocabs = [
        _normalized_vocabulary(
            " ".join(str(h.get(key) or "") for key in ("text", "value", "quote"))
        )
        for h in usable_hooks
    ]
    hook_union = set().union(*hook_vocabs)
    blk_vocab = {_normal_token(token) for token in _approved_blk_vocabulary(facts)}
    firm_vocab = _normalized_vocabulary(firm_name)

    for sentence in sentences:
        terms = _claim_terms(sentence)
        firm_terms = [
            term for term in terms
            if _normal_token(term.phrase) not in blk_vocab | firm_vocab
        ]

        # Pick the hook that best explains this sentence, not merely the first
        # that covers its vocabulary. Hooks about the same firm share words like
        # the firm's name, so first-match credits sentences to the wrong URL,
        # and a wrong source_url in the evidence block is worse than none.
        sentence_vocab = _normalized_vocabulary(sentence)
        content_vocab = {
            token for token in sentence_vocab
            if token not in blk_vocab | firm_vocab | _CONTENT_STOPWORDS | _INTERPRETIVE_VOCAB
        }
        overlaps = [len(content_vocab & hook_vocab) for hook_vocab in hook_vocabs]
        best_overlap = max(overlaps, default=0)
        best_index = overlaps.index(best_overlap) if best_overlap else None
        best_unsupported_claims = [
            term for term in firm_terms
            if _normal_token(term.phrase) not in hook_union
        ]

        pattern_claims = _unsupported_pattern_claims(sentence, hook_vocabs)
        for claim in pattern_claims:
            _append_unique_claim(best_unsupported_claims, claim)
        mentions_firm_name = bool(firm_vocab & sentence_vocab)
        mentions_blk = "blk" in sentence_vocab
        firm_reference = mentions_firm_name or (
            bool(_FIRM_REFERENCE.search(sentence)) and not mentions_blk
        )
        # A factual firm sentence needs meaningful lexical contact with at
        # least one selected hook.  One token is enough for a very short claim;
        # longer claims require two, while connective BLK language is ignored.
        required_overlap = 1 if len(content_vocab) <= 3 else 2
        insufficient_support = firm_reference and bool(content_vocab) and best_overlap < required_overlap
        if insufficient_support:
            unsupported_words = sorted(content_vocab - hook_union)[:6]
            actual_tokens: dict[str, str] = {}
            for token in _WORD.findall(sentence):
                actual_tokens.setdefault(_normal_token(token), token.strip("."))
            for word in unsupported_words:
                _append_unique_claim(
                    best_unsupported_claims,
                    ClaimDetection(
                        phrase=actual_tokens.get(word, word),
                        claim_type="lexical_claim",
                    ),
                )

        best_unsupported = [claim.phrase for claim in best_unsupported_claims]

        # A sentence making no firm-specific claim needs no hook, but it also
        # cannot be the whole paragraph, which the sentence-count gate covers.
        if not firm_reference and not firm_terms and not pattern_claims:
            report.sentences.append(
                SentenceEvidence(sentence=sentence, source_url=None, hook_index=None)
            )
            continue

        relevant_indices = [
            index for index, overlap in enumerate(overlaps)
            if overlap > 0 and (overlap >= 2 or index == best_index)
        ]
        if best_index is not None and best_index not in relevant_indices:
            relevant_indices.insert(0, best_index)
        source_urls = [firm_claim_source_of(usable_hooks[index]) for index in relevant_indices]
        hook_ids = [
            str(usable_hooks[index].get("research_hook_id") or "")
            for index in relevant_indices
            if usable_hooks[index].get("research_hook_id")
        ]
        unsupported_phrase = ", ".join(best_unsupported) if best_unsupported else None

        evidence = SentenceEvidence(
            sentence=sentence,
            source_url=source_urls[0] if source_urls and not best_unsupported else None,
            hook_index=relevant_indices[0] if relevant_indices and not best_unsupported else None,
            unsupported_terms=best_unsupported,
            source_urls=source_urls if not best_unsupported else [],
            hook_indices=relevant_indices if not best_unsupported else [],
            hook_ids=hook_ids if not best_unsupported else [],
            unsupported_phrase=unsupported_phrase,
            unsupported_claims=best_unsupported_claims,
        )
        report.sentences.append(evidence)

        if best_unsupported:
            report.violations.append(
                f"Sentence {len(report.sentences)} contains a factual claim not "
                "supported by the selected research hooks. Unsupported phrase: "
                f"{unsupported_phrase!r}; no source_url backs it. Sentence: "
                f"{sentence!r}. Every "
                "firm-specific claim must trace to selected stored evidence (rule 1)."
            )

    return report


def build_evidence_block(report: ProvenanceReport, hooks: list[dict[str, Any]]) -> str:
    """Render the per-sentence evidence block that makes review take 30 seconds."""
    usable = [h for h in hooks if firm_claim_source_of(h)]
    lines = ["## Evidence", ""]
    for i, ev in enumerate(report.sentences, start=1):
        lines.append(f"{i}. {ev.sentence}")
        indices = ev.hook_indices or ([ev.hook_index] if ev.hook_index is not None else [])
        if not indices:
            lines.append("   Source: no firm-specific claim in this sentence")
        else:
            for index in indices:
                hook = usable[index]
                quote = hook.get("quote") or hook.get("text") or ""
                lines.append(f"   Source: {firm_claim_source_of(hook)}")
                if hook.get("research_hook_id"):
                    lines.append(f"   Hook ID: {hook['research_hook_id']}")
                if quote:
                    lines.append(f"   Supporting text: {quote.strip()[:300]}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
