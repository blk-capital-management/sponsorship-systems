"""Stable, server-derived identifiers for research alignment hooks.

Research artifacts remain schema-valid and unchanged on disk.  The dashboard
decorates them at the API boundary, and draft generation resolves submitted
IDs back against the stored artifact.  A browser can therefore select evidence
but can never submit a replacement URL or hook body.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

from common.namespaces import firm_claim_source_of


class ResearchHookSelectionError(ValueError):
    """Raised when selected evidence does not belong to the stored artifact."""


def research_hook_id(
    artifact: Mapping[str, Any], hook: Mapping[str, Any]
) -> str:
    """Return a deterministic content identity for one stored research hook."""
    identity = {
        "firm": str(artifact.get("firm") or "").strip(),
        "firm_slug": str(artifact.get("firm_slug") or "").strip(),
        "source_url": firm_claim_source_of(hook),
        "text": str(hook.get("text") or hook.get("value") or "").strip(),
        "quote": str(hook.get("quote") or "").strip(),
        "basis": str(hook.get("basis") or "").strip(),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return f"rhook_{digest}"


def artifact_with_hook_ids(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Return an API-safe copy with IDs added to its alignment hooks."""
    return {
        **artifact,
        "alignment_hooks": [
            {**hook, "research_hook_id": research_hook_id(artifact, hook)}
            for hook in artifact.get("alignment_hooks", [])
        ],
    }


def resolve_selected_hooks(
    artifact: Mapping[str, Any], selected_ids: Iterable[str] | None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve browser-selected IDs against stored hooks, never client URLs.

    ``None`` keeps command-line and existing internal callers compatible by
    selecting every sourced hook.  An explicit empty selection is valid and
    resolves to no hooks: research is optional, and a firm with nothing citable
    still gets a draft (the review item records it as ungrounded).
    """
    hooks = [dict(hook) for hook in artifact.get("alignment_hooks", [])]
    indexed = {
        research_hook_id(artifact, hook): {
            **hook,
            "research_hook_id": research_hook_id(artifact, hook),
        }
        for hook in hooks
        if firm_claim_source_of(hook)
    }
    if selected_ids is None:
        ids = list(indexed)
    else:
        ids = [str(value).strip() for value in selected_ids if str(value).strip()]
        if len(ids) != len(set(ids)):
            raise ResearchHookSelectionError(
                "Each supporting research hook may be selected only once."
            )

    unknown = [hook_id for hook_id in ids if hook_id not in indexed]
    if unknown:
        raise ResearchHookSelectionError(
            "One or more selected research hooks no longer match the stored artifact. "
            "Reopen the draft form and select the current sourced hooks."
        )
    return [indexed[hook_id] for hook_id in ids], ids
