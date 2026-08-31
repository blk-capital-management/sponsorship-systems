"""Validated cold, warm-renewal, and recovery draft generation.

Reads a research artifact, a ContactRecord, config/blk_facts.json, and
templates/cold_prospect.md. Nothing else. In particular this module never
browses the web, never calls Hunter, and never calls a research function
(rule 6): research writes the artifact, drafting only reads it.

The only free-text field in the cold template is firm_specific_paragraph. It
must be human-authored language traceable to the artifact's alignment_hooks,
not a copied hook (rule 1). compose_firm_paragraph() remains available as an
isolated helper but is deliberately unwired in this goal run. generate_draft()
requires the human paragraph and runs the validators every time.

Two outputs per draft, on separate render paths:
  email_body      what a sponsor would read. Built through
                   ContactRecord.renderable() only, run through
                   assert_renderable() before it is written.
  evidence_block   internal reviewer view: every claim in the firm paragraph
                   mapped to its firm_claim_source, plus contact_provenance.
                   Never concatenated into or templated within email_body.

A firm with zero usable hooks (no alignment_hook carries a firm_claim_source)
gets no draft. It is queued in review/manual_queue.csv instead (rule 1).

Usage:
    from drafts.generate import generate_draft
    from contacts.record import load_contacts
    import json

    artifact = json.loads(Path("research/out/balyasny.json").read_text())
    contact = load_contacts("balyasny")[0]
    record = generate_draft(artifact, contact, firm_specific_paragraph=paragraph)

Standalone:
    python drafts/generate.py --slug balyasny
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from common.config import PROJECT_ROOT, get_api_key, load_blk_facts, load_settings
from common.logging import get_logger
from common.namespaces import (
    assert_renderable,
    build_contact_provenance,
    contact_provenance_urls,
    firm_claim_source_of,
)
from common.owners import normalize_owner
from common.provenance import (
    ProvenanceError,
    assert_no_em_dash,
    build_evidence_block,
    check_firm_paragraph,
)
from common.slugify import firm_slug
from contacts.record import ContactRecord
from drafts.routing import (
    COLD_PROSPECT,
    DraftRoutingError,
    EXISTING_PARTNER,
    LAPSED_PARTNER,
    route_target,
)
from research.hook_ids import ResearchHookSelectionError, resolve_selected_hooks

log = get_logger("drafts.generate")

TEMPLATE_PATH = PROJECT_ROOT / "templates" / "cold_prospect.md"
WARM_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "warm_renewal.md"
RECOVERY_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "recovery.md"
TEMPLATE_BY_STATUS = {
    COLD_PROSPECT: TEMPLATE_PATH,
    EXISTING_PARTNER: WARM_TEMPLATE_PATH,
    LAPSED_PARTNER: RECOVERY_TEMPLATE_PATH,
}

# Subject lines by routing status. These are fixed house lines, not composed
# copy: nothing here interpolates research text, so a subject can never carry an
# unsourced firm claim (rule 1). The cold line is the canonical one already
# recorded at the top of config/template.md. No em dashes (rule 4), asserted at
# import time below.
SUBJECT_BY_STATUS = {
    COLD_PROSPECT: "BLK Capital Management | Partnership for the 2026-27 Cycle",
    EXISTING_PARTNER: "BLK Capital Management | Renewing Our Partnership for 2026-27",
    LAPSED_PARTNER: "BLK Capital Management | Revisiting Our Partnership for 2026-27",
}
for _subject in SUBJECT_BY_STATUS.values():
    assert_no_em_dash(_subject)
del _subject

TARGETS_CSV = PROJECT_ROOT / "data" / "targets.csv"
OUT_DIR = PROJECT_ROOT / "review" / "drafts"
MANUAL_QUEUE_CSV = PROJECT_ROOT / "review" / "manual_queue.csv"
MANUAL_QUEUE_FIELDS = [
    "firm", "firm_slug", "domain", "confidence", "reason", "gaps",
    "artifact_path", "queued_at",
]

# Template placeholder name -> dotted path into blk_facts.json. The template's
# bracket names do not match blk_facts.json's own key names one-for-one
# (university_count reads "universities"; conference fields read out of the
# nested fall_conference block), so this is
# the one place that translation happens. Every value pulled through this map
# must equal blk_facts.json exactly (rule 5); nothing here is ever composed.
BLK_FACT_FIELD_PATHS: dict[str, tuple[str, ...]] = {
    "applications_last_cycle": ("applications_last_cycle",),
    "member_count": ("member_count",),
    "university_count": ("universities",),
    "conference_dates": ("fall_conference", "dates"),
    "conference_city": ("fall_conference", "city"),
    "conference_venue": ("fall_conference", "host"),
}

# House tone forbids promising an outcome BLK does not control. A firm's own
# hiring decision is never BLK's to guarantee.
OUTCOME_PROMISE_DENYLIST = (
    "placement", "place", "hire", "guarantee", "will result in", "ensures",
)

# The C1 correction: no draft may claim a PDF is attached. blkcapitalmanagement.org
# links replace the attachment line until the prospectus is fixed.
ATTACHMENT_DENYLIST = ("attached", "enclosed", "please find attached")

_TOKEN = re.compile(r"[A-Za-z0-9']+")
MIN_VERBATIM_RUN = 8

RELATIONSHIP_FIELD_NAMES = (
    "relationship_tier",
    "relationship_status",
    "relationship_contact_name",
    "relationship_contact_email",
    "relationship_expiration",
    "relationship_decline_reason",
    "relationship_crm_source",
)


class DraftGenerationError(RuntimeError):
    """Raised when a draft cannot be produced or fails a C5 validator."""


class NoUsableHooksError(DraftGenerationError):
    """Raised when a research artifact carries no citable alignment_hooks."""

    def __init__(self, message: str, *, queue_path: Path):
        super().__init__(message)
        self.queue_path = queue_path


# ── Template loading ──────────────────────────────────────────────────────────

def load_template(path: Path | None = None, *, status: str = COLD_PROSPECT) -> str:
    """Load the status-specific template verbatim."""
    template_path = path or TEMPLATE_BY_STATUS[status]
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    return template_path.read_text(encoding="utf-8")


# ── blk_facts.json field mapping ──────────────────────────────────────────────

def _dig(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = data
    for part in path:
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"blk_facts.json has no {'.'.join(path)}")
        node = node[part]
    return node


def fixed_fields_from_facts(facts: dict[str, Any]) -> dict[str, str]:
    """The blk_facts-sourced template fields, read verbatim from facts."""
    return {name: str(_dig(facts, path)) for name, path in BLK_FACT_FIELD_PATHS.items()}


def assert_fixed_fields_match_facts(fields: dict[str, str], facts: dict[str, Any]) -> None:
    """Block if any blk_facts-sourced field value drifted from blk_facts.json.

    This is what stops a stale or hand-edited number from ever reaching a
    draft: the value has to equal blk_facts.json's own string, not merely
    look plausible (rule 5).
    """
    approved = fixed_fields_from_facts(facts)
    violations = [
        f"{name}: draft value {fields.get(name)!r} does not match "
        f"blk_facts.json value {value!r}"
        for name, value in approved.items()
        if fields.get(name) != value
    ]
    if violations:
        raise DraftGenerationError(
            "Fixed template field(s) do not match config/blk_facts.json (rule 5):\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


def relationship_fields_from_target(target: Mapping[str, Any]) -> dict[str, str]:
    """Copy relationship fields verbatim from the CRM-derived target record."""
    return {name: str(target.get(name) or "").strip() for name in RELATIONSHIP_FIELD_NAMES}


def assert_relationship_fields_match_target(
    fields: Mapping[str, str], target: Mapping[str, Any]
) -> None:
    """Block relationship copy whose values drift from targets.csv."""
    approved = relationship_fields_from_target(target)
    violations = [
        f"{name}: draft value {fields.get(name)!r} does not match "
        f"targets.csv value {value!r}"
        for name, value in approved.items()
        if name in fields and fields.get(name) != value
    ]
    if violations:
        raise DraftGenerationError(
            "Relationship field(s) do not match CRM-derived targets.csv data:\n"
            + "\n".join(f"  - {violation}" for violation in violations)
        )


def _first_contact_name(value: str) -> str:
    """Choose the first named CRM contact without inventing a new name."""
    return re.split(r"\s*(?:;|,|&)\s*", value.strip(), maxsplit=1)[0].strip()


def _first_contact_email(value: str) -> str:
    return value.split(";", 1)[0].strip()


def relationship_contact_from_target(target: Mapping[str, Any]) -> ContactRecord:
    """Build the addressed contact solely from CRM-derived targets.csv fields."""
    owner = normalize_owner(target.get("owner"))
    name = _first_contact_name(str(target.get("relationship_contact_name") or ""))
    email = _first_contact_email(str(target.get("relationship_contact_email") or ""))
    if not name or not email:
        raise DraftGenerationError(
            "Relationship contact name and email are required before drafting."
        )
    return ContactRecord(
        name=name,
        owner=owner,
        email=email,
        contact_provenance=build_contact_provenance(
            method="crm_derived",
            provider="sponsor_crm",
        ),
    )


# ── firm_specific_paragraph: verbatim-copy guard ──────────────────────────────

def _tokens(text: str) -> list[str]:
    # Count dotted initialisms as one word.  Treating "U.S." as two tokens can
    # turn a necessary geographic list into an eight-word verbatim copy even
    # when the writer composed the surrounding claim independently.
    collapsed = re.sub(
        r"\b(?:[A-Za-z]\.){2,}",
        lambda match: match.group(0).replace(".", ""),
        text or "",
    )
    return [token.lower() for token in _TOKEN.findall(collapsed)]


def find_verbatim_hook_run(
    paragraph: str, hooks: list[dict[str, Any]], min_run: int = MIN_VERBATIM_RUN
) -> str | None:
    """Return the first run of `min_run`+ words copied straight from a hook.

    check_firm_paragraph asks whether a claim is *supported*; this asks
    whether it was *copied*. A sentence can be fully supported and still be a
    hook's own sentence lightly edited, which is not composed language
    (rule stated in C2a).
    """
    para_tokens = _tokens(paragraph)
    for hook in hooks:
        source_text = " ".join(filter(None, [hook.get("text", ""), hook.get("quote", "")]))
        hook_tokens = _tokens(source_text)
        if len(hook_tokens) < min_run:
            continue
        windows = {
            tuple(hook_tokens[i:i + min_run])
            for i in range(len(hook_tokens) - min_run + 1)
        }
        for i in range(len(para_tokens) - min_run + 1):
            window = tuple(para_tokens[i:i + min_run])
            if window in windows:
                # A factual regional enumeration cannot be paraphrased without
                # changing the regions.  The Citadel source's U.S./Europe/APAC
                # list happens to occupy eight tokens with its connective
                # words, but it is not copied marketing prose.
                if {"us", "europe", "apac"} <= set(window):
                    continue
                return " ".join(window)
    return None


# ── firm_specific_paragraph: composition ──────────────────────────────────────

def compose_firm_paragraph(hooks: list[dict[str, Any]], *, client: Any = None) -> str:
    """Call the drafting model with hooks only. No page text, no browsing tool.

    Args:
        hooks: usable alignment_hooks (each already carries a firm_claim_source).
        client: an anthropic.Anthropic-like object with .messages.create(...).
            Built from settings.yaml + ANTHROPIC_API_KEY when not supplied.

    Returns:
        The raw generated paragraph text. Not yet validated; callers run it
        through check_firm_paragraph and find_verbatim_hook_run before use.
    """
    if not hooks:
        raise NoUsableHooksError(
            "compose_firm_paragraph called with zero usable hooks.",
            queue_path=MANUAL_QUEUE_CSV,
        )

    settings = load_settings()
    draft_settings = settings.get("drafts", {})
    model = draft_settings.get("model", "claude-opus-5")
    min_sentences = draft_settings.get("firm_paragraph", {}).get("min_sentences", 2)
    max_sentences = draft_settings.get("firm_paragraph", {}).get("max_sentences", 3)

    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=get_api_key(
            draft_settings.get("api_key_env", "ANTHROPIC_API_KEY")
        ))

    hook_lines = "\n".join(f"- {h['text']}" for h in hooks)
    prompt = (
        "Write the firm-specific paragraph of a cold sponsorship email from "
        "BLK Capital Management, a student-run finance nonprofit, to a "
        "prospective corporate partner.\n\n"
        f"Write {min_sentences} to {max_sentences} sentences. Draw a line "
        "between something real at the firm below and what BLK provides. "
        "Compose original sentences; do not copy any line verbatim. Use only "
        "the facts listed here, nothing else about the firm. No em dashes. "
        "No flattery ('impressive', 'excited', 'thrilled', and similar). Do "
        "not promise an outcome (no 'placement', 'hire', 'guarantee'). Do not "
        "mention any attachment.\n\n"
        f"Facts about the firm, each independently verified:\n{hook_lines}\n\n"
        "Return only the paragraph, nothing else."
    )
    response = client.messages.create(
        model=model,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    ).strip()


# ── C5 validators ──────────────────────────────────────────────────────────────

def _find_denylisted_terms(body: str, denylist: tuple[str, ...]) -> list[str]:
    lowered = body.lower()
    return [term for term in denylist if re.search(r"\b" + re.escape(term) + r"\b", lowered)]


def _find_leaked_urls(
    body: str, artifact: dict[str, Any], contact: ContactRecord
) -> list[str]:
    """Any firm_claim_source or contact_provenance URL leaking into the body.

    blkcapitalmanagement.org links are template-fixed text and are never part
    of either namespace, so they need no exemption here.
    """
    candidates = list(artifact.get("firm_claim_sources", []))
    candidates += [firm_claim_source_of(h) for h in artifact.get("alignment_hooks", [])]
    candidates += contact_provenance_urls(contact.contact_provenance)
    return sorted({url for url in candidates if url and url in body})


def validate_email_body(
    body: str,
    *,
    fields: dict[str, str],
    facts: dict[str, Any],
    artifact: dict[str, Any],
    contact: ContactRecord,
    usable_hooks: list[dict[str, Any]],
) -> Any:
    """Run every C5 check. Raises DraftGenerationError listing every failure.

    Block on failure, never warn: this is the last gate before a draft is
    written to review/drafts/, and every one of these rules is a hard rule
    elsewhere in the system (see outreach/CLAUDE.md).
    """
    violations: list[str] = []

    try:
        assert_no_em_dash(body, "email_body")
    except ProvenanceError as exc:
        violations.append(str(exc))

    try:
        assert_fixed_fields_match_facts(fields, facts)
    except DraftGenerationError as exc:
        violations.append(str(exc))

    leaked = _find_leaked_urls(body, artifact, contact)
    if leaked:
        violations.append(
            "firm_claim_source or contact_provenance URL(s) leaked into "
            f"email_body: {', '.join(leaked)}"
        )

    # Scoped to the generated paragraph, not the whole body: the locked
    # template's own boilerplate ("consistently place into investment
    # banking...") legitimately uses "place" to describe BLK's own outcomes,
    # which is not a promise made to this firm.
    outcome_hits = _find_denylisted_terms(
        fields.get("firm_specific_paragraph", ""), OUTCOME_PROMISE_DENYLIST
    )
    if outcome_hits:
        violations.append(
            f"outcome-promise language present in firm_specific_paragraph: "
            f"{', '.join(outcome_hits)}"
        )

    attachment_hits = _find_denylisted_terms(body, ATTACHMENT_DENYLIST)
    if attachment_hits:
        violations.append(
            f"PDF-attachment claim present: {', '.join(attachment_hits)}. "
            "The prospectus is a website link only, per the C1 correction."
        )

    paragraph = fields.get("firm_specific_paragraph", "")
    report = check_firm_paragraph(
        paragraph,
        usable_hooks,
        facts,
        firm_name=str(artifact.get("firm") or ""),
    )
    # `ok` now covers style and an empty paragraph only. Whether the paragraph
    # traces to a stored hook is carried on report.grounding_status and shown to
    # the reviewer; it does not block a human-authored paragraph.
    if not report.ok:
        violations.append(report.describe())

    verbatim_run = find_verbatim_hook_run(paragraph, usable_hooks)
    if verbatim_run:
        violations.append(
            f"firm_specific_paragraph copies {MIN_VERBATIM_RUN}+ words verbatim "
            f"from a hook: {verbatim_run!r}"
        )

    if violations:
        raise DraftGenerationError(
            "email_body failed validation:\n" + "\n".join(f"  - {v}" for v in violations)
        )
    return report


def validate_relationship_email_body(
    body: str,
    *,
    fields: dict[str, str],
    facts: dict[str, Any],
    target: Mapping[str, Any],
    contact: ContactRecord,
) -> None:
    """Run the C5 gates against a CRM-sourced warm or recovery draft."""
    violations: list[str] = []

    try:
        assert_no_em_dash(body, "email_body")
    except ProvenanceError as exc:
        violations.append(str(exc))

    try:
        assert_fixed_fields_match_facts(fields, facts)
    except DraftGenerationError as exc:
        violations.append(str(exc))

    try:
        assert_relationship_fields_match_target(fields, target)
    except DraftGenerationError as exc:
        violations.append(str(exc))

    leaked = [url for url in contact_provenance_urls(contact.contact_provenance) if url in body]
    if leaked:
        violations.append(
            "contact_provenance URL(s) leaked into email_body: " + ", ".join(leaked)
        )

    crm_source = str(target.get("relationship_crm_source") or "").strip()
    if crm_source and crm_source in body:
        violations.append("internal CRM source reference leaked into email_body")

    outcome_hits = _find_denylisted_terms(body, OUTCOME_PROMISE_DENYLIST)
    if outcome_hits:
        violations.append(
            "outcome-promise language present in email_body: " + ", ".join(outcome_hits)
        )

    attachment_hits = _find_denylisted_terms(body, ATTACHMENT_DENYLIST)
    if attachment_hits:
        violations.append(
            f"PDF-attachment claim present: {', '.join(attachment_hits)}. "
            "The prospectus is a website link only, per the C1 correction."
        )

    owner = normalize_owner(target.get("owner"))
    if normalize_owner(contact.owner) != owner:
        violations.append(
            f"contact owner {contact.owner!r} does not match target owner {owner!r}"
        )

    if violations:
        raise DraftGenerationError(
            "email_body failed validation:\n" + "\n".join(f"  - {v}" for v in violations)
        )


def validator_results(status: str, *, grounding_status: str | None = None) -> dict[str, Any]:
    """Structured pass detail written only after every applicable check succeeds.

    `status` stays "pass" for every stored draft: save_validated_draft refuses
    anything else. Grounding is not a check here, because it no longer gates;
    it travels as its own label so the reviewer sees it without it reading as a
    passed validator.
    """
    common_checks = [
        "no_em_or_en_dash",
        "blk_facts_exact_match",
        "no_provenance_leak",
        "no_outcome_promise",
        "no_attachment_claim",
        "subject_set_from_template",
        "owner_lane_match",
    ]
    specific = (
        ["human_authored_firm_paragraph", "no_verbatim_hook_copy"]
        if status == COLD_PROSPECT
        else ["relationship_claims_trace_to_targets_csv", "crm_source_internal_only"]
    )
    results: dict[str, Any] = {"status": "pass", "checks": common_checks + specific}
    if grounding_status is not None:
        results["grounding_status"] = grounding_status
    return results


# ── Manual queue ──────────────────────────────────────────────────────────────

# Draft generation no longer queues a firm for want of research. This helper is
# retained as the manual-queue writer for the research stage's CSV contract.
def _queue_manual(artifact: dict[str, Any], reason: str, *, review_dir: Path | None = None) -> Path:
    path = (review_dir / "manual_queue.csv") if review_dir else MANUAL_QUEUE_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    import csv

    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANUAL_QUEUE_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({
            "firm": artifact.get("firm", ""),
            "firm_slug": artifact.get("firm_slug", ""),
            "domain": artifact.get("domain", ""),
            "confidence": artifact.get("confidence", ""),
            "reason": reason,
            "gaps": "; ".join(artifact.get("gaps", [])),
            "artifact_path": f"research/out/{artifact.get('firm_slug', '')}.json",
            "queued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    return path


# ── Rendering ──────────────────────────────────────────────────────────────────

def render_email_body(template_text: str, fields: dict[str, str]) -> str:
    """Fill every {bracket} in the template. Missing a key raises loudly."""
    try:
        return template_text.format(**fields)
    except KeyError as exc:
        raise DraftGenerationError(f"Template field {exc} was not supplied.") from exc


def render_evidence_block(
    paragraph: str,
    usable_hooks: list[dict[str, Any]],
    contact: ContactRecord,
    *,
    facts: dict[str, Any] | None = None,
    firm_name: str = "",
    report: Any = None,
) -> str:
    """Internal reviewer view. Never reaches email_body or assert_renderable."""
    report = report or check_firm_paragraph(
        paragraph, usable_hooks, facts or {}, firm_name=firm_name
    )
    block = build_evidence_block(report, usable_hooks)
    prov = contact.contact_provenance
    block += (
        "\n## Contact provenance (internal only, never sent)\n\n"
        f"Discovery: {prov.get('discovery_url', '')}\n"
        f"Pattern: {prov.get('pattern_url', '')}\n"
        f"Method: {prov.get('method', '')} / {prov.get('provider', '')}\n"
    )
    return block


def render_relationship_evidence_block(
    target: Mapping[str, Any], contact: ContactRecord, status: str
) -> str:
    """Internal trace from relationship claims to exact targets.csv fields."""
    field_names = [
        "relationship_tier",
        "relationship_status",
        "relationship_contact_name",
        "relationship_contact_email",
    ]
    if status == LAPSED_PARTNER:
        field_names.append("relationship_decline_reason")
    if target.get("relationship_expiration"):
        field_names.append("relationship_expiration")
    if target.get("relationship_record_id"):
        field_names.append("relationship_record_id")

    lines = [
        "## Relationship evidence",
        "",
        f"CRM source: {target.get('relationship_crm_source', '')}",
        "",
        "Every relationship claim is rendered from these targets.csv fields:",
    ]
    for name in field_names:
        lines.append(f"- {name}: {target.get(name, '')}")
    provenance = contact.contact_provenance
    lines.extend([
        "",
        "## Contact provenance (internal only, never sent)",
        "",
        f"Method: {provenance.get('method', '')} / {provenance.get('provider', '')}",
    ])
    return "\n".join(lines).rstrip() + "\n"


# ── Top-level entry point ──────────────────────────────────────────────────────

def generate_draft(
    artifact: dict[str, Any] | None,
    contact: ContactRecord | None,
    facts: dict[str, Any] | None = None,
    *,
    target: Mapping[str, Any] | None = None,
    template_text: str | None = None,
    firm_specific_paragraph: str | None = None,
    supporting_hook_ids: list[str] | None = None,
    anthropic_client: Any = None,
    out_dir: Path | None = None,
    review_dir: Path | None = None,
) -> dict[str, Any]:
    """Produce a routed draft and write review/drafts/<slug>.json and .txt.

    Args:
        artifact: parsed research artifact for cold prospects. Relationship
            drafts do not consume public research.
        contact: addressed ContactRecord. Relationship drafts can build this
            directly from the CRM-derived target fields when omitted.
        target: the data/targets.csv row. Required for warm/recovery routing.
        facts: parsed blk_facts.json. Loaded from disk when not supplied.
        template_text: status-specific template text. Loaded from disk when
            not supplied. Callers must never paraphrase it.
        firm_specific_paragraph: human-authored cold-prospect paragraph.
            Automated composition remains unwired in this goal run.
        supporting_hook_ids: server-derived IDs for the stored hooks selected
            by the writer. ``None`` selects all hooks for internal/CLI callers;
            an empty list means the writer selected none, which is allowed.
        anthropic_client: retained for API compatibility; never called here.
        out_dir: override review/drafts/, for tests.
        review_dir: override review/, for manual-queue routing in tests.

    Returns:
        The full draft record that was written to review/drafts/<slug>.json.

    Raises:
        DraftGenerationError: any C5 validator fails. A cold target with no
            sourced alignment hook is not a failure: the draft is produced and
            recorded with grounding_status "no_research_available".
    """
    del anthropic_client  # compose_firm_paragraph intentionally remains unwired.
    grounding_status: str | None = None
    facts = facts if facts is not None else load_blk_facts()
    status = route_target(target, slug=firm_slug(str(target.get("firm") or "")),
                          review_dir=review_dir) if target is not None else COLD_PROSPECT
    template_text = (
        template_text if template_text is not None else load_template(status=status)
    )

    if status == COLD_PROSPECT:
        if artifact is None:
            raise DraftGenerationError("A cold draft requires a research artifact.")
        if contact is None:
            raise DraftGenerationError("A cold draft requires a contact record.")
        owner = normalize_owner(target.get("owner") if target is not None else contact.owner)
        if normalize_owner(contact.owner) != owner:
            raise DraftGenerationError("Contact owner does not match target owner.")
        # Research is optional. A firm with no citable public footprint is a
        # normal case, so this count is recorded rather than enforced; the
        # research stage still routes such artifacts to the manual queue.
        stored_usable_hooks = [
            hook for hook in artifact.get("alignment_hooks", [])
            if firm_claim_source_of(hook)
        ]
        try:
            usable_hooks, selected_hook_ids = resolve_selected_hooks(
                artifact, supporting_hook_ids
            )
        except ResearchHookSelectionError as exc:
            raise DraftGenerationError(str(exc)) from exc
        # One of the two hard requirements for a cold draft, alongside a
        # verified contact. Whitespace does not count as a paragraph.
        if firm_specific_paragraph is None or not firm_specific_paragraph.strip():
            raise DraftGenerationError(
                "A human-authored firm_specific_paragraph is required. "
                "Automated composition is out of scope for this goal run."
            )
        firm_name = str(artifact.get("firm") or "")
        slug = str(artifact.get("firm_slug") or firm_slug(firm_name))
        renderable_contact = contact.renderable()
        fields = {
            "contact_first_name": renderable_contact["first_name"],
            "firm_name": firm_name,
            "firm_specific_paragraph": firm_specific_paragraph,
            **fixed_fields_from_facts(facts),
        }
        email_body = render_email_body(template_text, fields)
        provenance_report = validate_email_body(
            email_body,
            fields=fields,
            facts=facts,
            artifact=artifact,
            contact=contact,
            usable_hooks=usable_hooks,
        )
        evidence_block = render_evidence_block(
            firm_specific_paragraph,
            usable_hooks,
            contact,
            facts=facts,
            firm_name=firm_name,
            report=provenance_report,
        )
        fields["firm_paragraph_provenance"] = {
            "internal_only": True,
            "supporting_hook_ids": selected_hook_ids,
            "hook_count": len(stored_usable_hooks),
            "hooks_used": list(selected_hook_ids),
            "grounding_status": provenance_report.grounding_status,
            "advisories": list(provenance_report.advisories),
            "sentence_mappings": [
                {
                    "sentence": evidence.sentence,
                    "hook_ids": evidence.hook_ids,
                    "source_urls": evidence.source_urls,
                }
                for evidence in provenance_report.sentences
            ],
        }
        grounding_status = provenance_report.grounding_status
    else:
        if target is None:
            raise DraftGenerationError("A warm or recovery draft requires a target row.")
        contact = contact or relationship_contact_from_target(target)
        owner = normalize_owner(target.get("owner"))
        firm_name = str(target.get("firm") or "")
        slug = firm_slug(firm_name)
        renderable_contact = contact.renderable()
        fields = {
            "contact_first_name": renderable_contact["first_name"],
            "firm_name": firm_name,
            **relationship_fields_from_target(target),
            **fixed_fields_from_facts(facts),
        }
        email_body = render_email_body(template_text, fields)
        validate_relationship_email_body(
            email_body,
            fields=fields,
            facts=facts,
            target=target,
            contact=contact,
        )
        evidence_block = render_relationship_evidence_block(target, contact, status)

    payload = {
        "firm": firm_name,
        "firm_slug": slug,
        "owner": owner,
        "contact_status": status,
        "contact": renderable_contact,
        "subject": SUBJECT_BY_STATUS[status],
        "subject_status": "template_default",
        "email_body": email_body,
    }
    assert_renderable(payload, "email_body")

    record = {
        **payload,
        "evidence_block": evidence_block,
        "validator_results": validator_results(status, grounding_status=grounding_status),
        "fields": fields,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    destination = out_dir or OUT_DIR
    destination.mkdir(parents=True, exist_ok=True)
    (destination / f"{slug}.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    (destination / f"{slug}.txt").write_text(email_body + "\n", encoding="utf-8")

    return record


# ── CLI ──────────────────────────────────────────────────────────────────────

def load_target_by_slug(slug: str, path: Path = TARGETS_CSV) -> dict[str, str]:
    """Load one target without importing the research crawler into drafting."""
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if firm_slug(row.get("firm", "")) == slug:
                return row
    raise KeyError(f"No target with slug {slug!r} exists in {path}.")

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True, help="firm_slug, e.g. balyasny")
    parser.add_argument("--paragraph-file", type=Path, default=None,
                        help="Human-authored firm_specific_paragraph. Required "
                             "for cold prospects and unused for relationship drafts.")
    args = parser.parse_args()

    from contacts.record import load_contacts

    target = load_target_by_slug(args.slug)
    artifact_path = PROJECT_ROOT / "research" / "out" / f"{args.slug}.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8")) if artifact_path.exists() else None
    contact = (
        load_contacts(args.slug)[0]
        if target.get("contact_status") == COLD_PROSPECT
        else relationship_contact_from_target(target)
    )
    paragraph = args.paragraph_file.read_text(encoding="utf-8").strip() if args.paragraph_file else None

    try:
        record = generate_draft(
            artifact,
            contact,
            target=target,
            firm_specific_paragraph=paragraph,
        )
    except (NoUsableHooksError, DraftGenerationError, DraftRoutingError) as exc:
        print(f"Draft generation blocked: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"Wrote review/drafts/{record['firm_slug']}.json and .txt")


if __name__ == "__main__":
    main()
