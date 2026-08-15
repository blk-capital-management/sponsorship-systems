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
class SentenceEvidence:
    """One paragraph sentence and the hook that backs it."""
    sentence: str
    source_url: str | None
    hook_index: int | None
    unsupported_terms: list[str] = field(default_factory=list)

    @property
    def supported(self) -> bool:
        return self.source_url is not None and not self.unsupported_terms


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


def _claim_terms(sentence: str) -> list[str]:
    """Return tokens that assert something checkable: proper nouns and numbers.

    The first token of a sentence is capitalized by grammar, not by meaning, so
    it only counts when it is not an ordinary word.
    """
    tokens = _WORD.findall(sentence)
    terms: list[str] = []
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
            terms.append(token.strip("."))
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


def check_firm_paragraph(
    paragraph: str,
    hooks: list[dict[str, Any]],
    facts: dict[str, Any],
    *,
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
        _vocabulary([h.get("text") or h.get("value") or ""]) for h in usable_hooks
    ]
    blk_vocab = _approved_blk_vocabulary(facts)

    for sentence in sentences:
        terms = _claim_terms(sentence)
        firm_terms = [t for t in terms if t.lower().strip(".") not in blk_vocab]

        # Pick the hook that best explains this sentence, not merely the first
        # that covers its vocabulary. Hooks about the same firm share words like
        # the firm's name, so first-match credits sentences to the wrong URL,
        # and a wrong source_url in the evidence block is worse than none.
        sentence_vocab = _vocabulary([sentence])
        best_index: int | None = None
        best_unsupported: list[str] = firm_terms
        best_overlap = -1
        for index, hook_vocab in enumerate(hook_vocabs):
            unsupported = [
                t for t in firm_terms if t.lower().strip(".") not in hook_vocab
            ]
            overlap = len(sentence_vocab & hook_vocab)
            better = (
                best_index is None
                or len(unsupported) < len(best_unsupported)
                or (len(unsupported) == len(best_unsupported) and overlap > best_overlap)
            )
            if better:
                best_index, best_unsupported, best_overlap = index, unsupported, overlap

        # A sentence making no firm-specific claim needs no hook, but it also
        # cannot be the whole paragraph, which the sentence-count gate covers.
        if not firm_terms:
            report.sentences.append(
                SentenceEvidence(sentence=sentence, source_url=None, hook_index=None)
            )
            continue

        evidence = SentenceEvidence(
            sentence=sentence,
            source_url=(
                firm_claim_source_of(usable_hooks[best_index])
                if not best_unsupported else None
            ),
            hook_index=best_index if not best_unsupported else None,
            unsupported_terms=best_unsupported,
        )
        report.sentences.append(evidence)

        if best_unsupported:
            report.violations.append(
                f"no source_url backs {', '.join(repr(t) for t in best_unsupported)} "
                f"in sentence: {sentence!r}. Every firm-specific claim must trace to "
                "a research hook (rule 1)."
            )

    return report


def build_evidence_block(report: ProvenanceReport, hooks: list[dict[str, Any]]) -> str:
    """Render the per-sentence evidence block that makes review take 30 seconds."""
    usable = [h for h in hooks if firm_claim_source_of(h)]
    lines = ["## Evidence", ""]
    for i, ev in enumerate(report.sentences, start=1):
        lines.append(f"{i}. {ev.sentence}")
        if ev.hook_index is None:
            lines.append("   Source: no firm-specific claim in this sentence")
        else:
            hook = usable[ev.hook_index]
            quote = hook.get("quote") or hook.get("text") or ""
            lines.append(f"   Source: {ev.source_url}")
            if quote:
                lines.append(f"   Supporting text: {quote.strip()[:300]}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
