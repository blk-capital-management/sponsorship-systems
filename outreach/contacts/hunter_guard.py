"""Scope, budget, and audit controls for every Hunter API call.

Hunter credits are finite and the free tier is small, so an unbounded loop over
28 firms can exhaust a month's balance in one run. Three controls apply to every
call, from every script:

  1. Scope. Domain Search and Email Finder may only be called with a domain that
     already has a row in data/targets.csv. Those endpoints look up contacts at
     firms already chosen; they have no business surfacing a domain nobody
     decided to target. Discover is the only endpoint permitted to surface a new
     domain, and only into review/prospects.csv.

  2. Budget. MAX_HUNTER_CALLS_PER_RUN hard-stops the run by raising. A warning
     would be read after the credits were already spent.

  3. Audit. Every call appends to contacts/hunter_usage.csv before the request
     is made, so a crash mid-run still leaves a record of what was spent.

Usage:
    from contacts.hunter_guard import HunterGuard
    guard = HunterGuard.from_settings(settings)
    guard.authorize("domain-search", "bamfunds.com")
"""

import csv
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common.config import PROJECT_ROOT, load_settings
from common.logging import get_logger

log = get_logger("contacts.hunter_guard")

USAGE_LOG_PATH = PROJECT_ROOT / "contacts" / "hunter_usage.csv"
USAGE_FIELDS = ["timestamp", "endpoint", "domain", "credits", "run_id", "note"]

DEFAULT_MAX_CALLS_PER_RUN = 10

# Endpoints that may only touch a domain already in targets.csv.
TARGET_SCOPED_ENDPOINTS = frozenset({"domain-search", "email-finder", "email-verifier"})

# The only endpoint allowed to surface a domain nobody has targeted yet. It is
# never called from the Phase 3 contact path.
PROSPECTING_ENDPOINTS = frozenset({"discover"})

ALLOWED_ENDPOINTS = TARGET_SCOPED_ENDPOINTS | PROSPECTING_ENDPOINTS | {"account"}

# Credits charged per call, used for the running total shown before spend.
CREDIT_COST = {
    "domain-search": 1,
    "email-finder": 1,
    "email-verifier": 1,
    "discover": 1,
    "account": 0,
}


class HunterScopeError(RuntimeError):
    """Raised when a call is attempted outside its permitted scope."""


class HunterBudgetError(RuntimeError):
    """Raised when a run would exceed its Hunter call cap."""


def normalize_domain(domain: str) -> str:
    return (domain or "").strip().lower().removeprefix("www.")


def load_target_domains(targets_path: Path | None = None) -> set[str]:
    """Every domain with a row in targets.csv."""
    path = targets_path or (PROJECT_ROOT / "data" / "targets.csv")
    if not path.exists():
        raise FileNotFoundError(f"targets.csv not found: {path}")
    with path.open(newline="", encoding="utf-8") as fh:
        return {
            normalize_domain(row["domain"])
            for row in csv.DictReader(fh)
            if row.get("domain", "").strip()
        }


class HunterGuard:
    """Authorizes, counts, and logs Hunter API calls for one run."""

    def __init__(
        self,
        max_calls: int = DEFAULT_MAX_CALLS_PER_RUN,
        target_domains: set[str] | None = None,
        usage_log: Path | None = None,
    ):
        self.max_calls = int(max_calls)
        self.target_domains = (
            target_domains if target_domains is not None else load_target_domains()
        )
        self.usage_log = usage_log or USAGE_LOG_PATH
        self.calls_made = 0
        self.credits_spent = 0
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    @classmethod
    def from_settings(cls, settings: dict[str, Any] | None = None) -> "HunterGuard":
        settings = settings or load_settings()
        cfg = settings.get("contacts", {}).get("hunter", {}) or {}
        # The environment overrides config so a run can be tightened without
        # editing a tracked file.
        max_calls = int(
            os.getenv("MAX_HUNTER_CALLS_PER_RUN")
            or cfg.get("max_calls_per_run", DEFAULT_MAX_CALLS_PER_RUN)
        )
        return cls(max_calls=max_calls)

    # ── Authorization ─────────────────────────────────────────────────────────

    def authorize(self, endpoint: str, domain: str | None = None) -> None:
        """Raise unless this call is in scope and within budget.

        Called before the request goes out, never after.
        """
        if endpoint not in ALLOWED_ENDPOINTS:
            raise HunterScopeError(
                f"Endpoint {endpoint!r} is not permitted. Allowed: "
                f"{', '.join(sorted(ALLOWED_ENDPOINTS))}."
            )

        if endpoint in TARGET_SCOPED_ENDPOINTS:
            normalized = normalize_domain(domain or "")
            if not normalized:
                raise HunterScopeError(f"{endpoint} requires a domain.")
            if normalized not in self.target_domains:
                # Refused and logged, not silently skipped.
                self._log(endpoint, normalized, credits=0,
                          note="REJECTED: domain not in targets.csv")
                raise HunterScopeError(
                    f"Refusing to call Hunter {endpoint} for {normalized!r}: it has "
                    "no row in data/targets.csv. Only Discover may surface a new "
                    "domain, and only into review/prospects.csv."
                )

        if self.calls_made >= self.max_calls:
            raise HunterBudgetError(
                f"Hunter call cap reached ({self.max_calls} calls this run). "
                f"Stopping before making another. Raise "
                "contacts.hunter.max_calls_per_run in settings.yaml or set "
                "MAX_HUNTER_CALLS_PER_RUN to continue."
            )

    def record(self, endpoint: str, domain: str | None, note: str = "") -> None:
        """Count and log a call that has been authorized and is about to go out."""
        credits = CREDIT_COST.get(endpoint, 1)
        self.calls_made += 1
        self.credits_spent += credits
        self._log(endpoint, normalize_domain(domain or ""), credits, note)

    def record_skip(self, endpoint: str, domain: str | None, note: str = "") -> None:
        """Record a call that was never made. Zero credits, no call counted.

        A skip is as much a part of the spend record as a call is, so the usage
        log answers both "where did the credits go" and "where were they saved".
        """
        self._log(endpoint, normalize_domain(domain or ""), 0, note)

    def _log(self, endpoint: str, domain: str, credits: int, note: str) -> None:
        self.usage_log.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.usage_log.exists()
        with self.usage_log.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=USAGE_FIELDS)
            if is_new:
                writer.writeheader()
            writer.writerow({
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "endpoint": endpoint,
                "domain": domain,
                "credits": credits,
                "run_id": self.run_id,
                "note": note,
            })

    # ── Reporting ─────────────────────────────────────────────────────────────

    def historical_credits(self) -> int:
        """Total credits recorded in the usage log across all previous runs."""
        if not self.usage_log.exists():
            return 0
        with self.usage_log.open(newline="", encoding="utf-8") as fh:
            return sum(int(row.get("credits") or 0) for row in csv.DictReader(fh))

    def print_budget(self, account: dict[str, Any] | None = None) -> None:
        """Show spend before it happens, not after."""
        log.info("Hunter budget for this run: cap %s call(s), %s used so far.",
                 self.max_calls, self.calls_made)
        log.info("  Credits logged across all previous runs: %s",
                 self.historical_credits())
        if account:
            available = account.get("requests", {}).get("searches", {})
            used, avail = available.get("used"), available.get("available")
            if used is not None and avail is not None:
                log.info("  Hunter account balance: %s of %s searches used, %s left.",
                         used, avail, max(0, avail - used))
        else:
            log.info("  Hunter account balance: not retrieved.")
