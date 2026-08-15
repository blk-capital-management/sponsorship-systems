"""Hunter.io email verifier.

Docs: https://hunter.io/api-documentation/v2#email-verifier

Hunter returns a `score` of 0-100 plus a `result` of deliverable, risky, or
undeliverable. The score is used directly against the configured threshold, and
an explicit undeliverable is forced to 0 no matter what score accompanies it.
"""

from typing import Any

import requests

from common.logging import get_logger
from common.namespaces import normalize_contact_url
from contacts.hunter_guard import HunterBudgetError, HunterScopeError
from contacts.providers.base import (
    ProviderNotConfigured,
    VerificationProvider,
    VerificationResult,
)

log = get_logger("contacts.providers.hunter")

ENDPOINT = "https://api.hunter.io/v2/email-verifier"
DOMAIN_SEARCH_ENDPOINT = "https://api.hunter.io/v2/domain-search"
EMAIL_FINDER_ENDPOINT = "https://api.hunter.io/v2/email-finder"
ACCOUNT_ENDPOINT = "https://api.hunter.io/v2/account"

# Hunter's Discover endpoint is deliberately absent from this module's contact
# lookup path. Discover surfaces firms nobody has targeted yet, which is a
# prospecting job, not a contact-lookup job. It lives in contacts/prospect.py
# and writes only to review/prospects.csv.

# Hunter uses the same {first}/{last}/{f}/{l} tokens this project does, so its
# pattern string maps across directly. Anything else is not understood and is
# rejected rather than guessed at.
_KNOWN_TOKENS = ("{first}", "{last}", "{f}", "{l}")

# The free plan caps domain-search results at 10. Asking for more is a hard 400,
# not a silent truncation.
DOMAIN_SEARCH_LIMIT = 10

# Hunter attributes many addresses to LinkedIn, usually as a Google search
# scoped to linkedin.com. Rule 3 keeps LinkedIn out of this pipeline, so those
# attributions cannot be used as a source_url. A pattern supported only by
# LinkedIn-derived sources is therefore treated as unsourced.
LINKEDIN_SOURCE_MARKERS = ("linkedin.com", "lnkd.in", "site%3alinkedin", "site:linkedin")


def is_linkedin_derived(source: dict[str, Any]) -> bool:
    """Whether a Hunter source entry traces back to LinkedIn."""
    haystack = f"{source.get('domain', '')} {source.get('uri', '')}".lower()
    return any(marker in haystack for marker in LINKEDIN_SOURCE_MARKERS)


class HunterProvider(VerificationProvider):
    name = "hunter"

    def __init__(self, api_key: str | None = None, timeout: int = 20, guard=None,
                 reject_linkedin: bool | None = None, departments: str | None = None):
        super().__init__(api_key, timeout)
        if not self.api_key:
            raise ProviderNotConfigured(
                "Hunter is selected but HUNTER_API_KEY is not set. Add it to .env "
                "(see .env.example), or set contacts.verification.provider to "
                "'dryrun' to run without a provider."
            )
        # Every call is scoped, counted, and logged. Constructed lazily so a
        # provider built in a test does not need targets.csv on disk.
        self.guard = guard
        if self.guard is None:
            from contacts.hunter_guard import HunterGuard

            self.guard = HunterGuard.from_settings()

        from common.config import load_settings

        cfg = (load_settings().get("contacts", {}).get("hunter", {})) or {}
        self.reject_linkedin = (
            cfg.get("reject_linkedin_derived_sources", False)
            if reject_linkedin is None else reject_linkedin
        )
        self.departments = (
            cfg.get("domain_search_departments", "") if departments is None
            else departments
        )

    def _call(self, endpoint_name: str, url: str, params: dict[str, Any],
              domain: str | None) -> Any:
        """Authorize, log, then make one Hunter request.

        The call is recorded before the request goes out, so a crash mid-run
        still leaves an accurate record of what was spent.
        """
        self.guard.authorize(endpoint_name, domain)
        self.guard.record(endpoint_name, domain)
        return requests.get(url, params={**params, "api_key": self.api_key},
                            timeout=self.timeout)

    def account(self) -> dict[str, Any] | None:
        """Fetch the account's remaining balance. Costs no credits."""
        try:
            response = self._call("account", ACCOUNT_ENDPOINT, {}, None)
            if response.status_code >= 400:
                return None
            return response.json().get("data")
        except Exception as exc:
            log.debug("Could not read Hunter account balance: %s", exc)
            return None

    def find_email(self, domain: str, first_name: str, last_name: str
                   ) -> dict[str, Any] | None:
        """Look up one person's address via Hunter's Email Finder.

        Scope-checked like every other target endpoint: the domain must already
        have a row in targets.csv.
        """
        try:
            response = self._call(
                "email-finder", EMAIL_FINDER_ENDPOINT,
                {"domain": domain, "first_name": first_name, "last_name": last_name},
                domain,
            )
        except (HunterScopeError, HunterBudgetError):
            raise
        except Exception as exc:
            log.warning("Hunter email-finder failed for %s %s at %s: %s",
                        first_name, last_name, domain, exc)
            return None

        if response.status_code >= 400:
            log.info("Hunter email-finder returned HTTP %s for %s at %s.",
                     response.status_code, last_name, domain)
            return None

        data = (response.json() or {}).get("data") or {}
        if not data.get("email"):
            return None

        sources = data.get("sources") or []
        return {
            "email": data["email"],
            "score": data.get("score"),
            "position": data.get("position") or "",
            # Normalized at capture, so contact_provenance never stores a
            # scheme-default port or a %20-riddled search string.
            "source_url": normalize_contact_url(
                next((s.get("uri") for s in sources if s.get("uri")), "")
            ),
        }

    def verify(self, email: str) -> VerificationResult:
        domain = email.rpartition("@")[2]
        try:
            response = self._call("email-verifier", ENDPOINT, {"email": email}, domain)
        except (HunterScopeError, HunterBudgetError):
            # Scope and budget are hard stops. Degrading them into a soft
            # per-address error is how a cap silently becomes a warning.
            raise
        except Exception as exc:
            log.warning("Hunter request failed for %s: %s", email, exc)
            return VerificationResult(email, 0, "error", self.name,
                                      error=type(exc).__name__)

        if response.status_code == 401:
            raise ProviderNotConfigured("Hunter rejected HUNTER_API_KEY (HTTP 401).")
        if response.status_code == 429:
            log.warning("Hunter rate limit hit for %s.", email)
            return VerificationResult(email, 0, "rate_limited", self.name,
                                      error="rate_limited")
        if response.status_code >= 400:
            return VerificationResult(email, 0, "error", self.name,
                                      error=f"http_{response.status_code}")

        return self._parse(email, response.json())

    def discover_pattern(self, domain: str) -> dict[str, Any] | None:
        """Look up a domain's email pattern via Hunter's domain-search endpoint.

        Docs: https://hunter.io/api-documentation/v2#domain-search

        Only addresses that Hunter can attribute to a public source page are
        used, so the resulting pattern still carries a real source_url and rule
        1 holds. Addresses with no `sources` entry are ignored.
        """
        try:
            params: dict[str, Any] = {"domain": domain, "limit": DOMAIN_SEARCH_LIMIT}
            # Filter server-side. The plan returns 10 addresses per domain, so an
            # unfiltered call spends the whole allowance on investment staff.
            if self.departments:
                params["department"] = self.departments
            response = self._call("domain-search", DOMAIN_SEARCH_ENDPOINT,
                                  params, domain)
        except (HunterScopeError, HunterBudgetError):
            raise
        except Exception as exc:
            log.warning("Hunter domain search failed for %s: %s", domain, exc)
            return None

        if response.status_code == 401:
            raise ProviderNotConfigured("Hunter rejected HUNTER_API_KEY (HTTP 401).")
        if response.status_code >= 400:
            log.warning("Hunter domain search for %s returned HTTP %s.",
                        domain, response.status_code)
            return None

        return self._parse_domain_search(domain, response.json())

    def _parse_domain_search(
        self, domain: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        data = payload.get("data", {}) or {}
        pattern = data.get("pattern")

        if not pattern or not any(token in pattern for token in _KNOWN_TOKENS):
            log.info("Hunter returned no usable pattern for %s.", domain)
            return None

        # Keep only addresses Hunter can point at a public, non-LinkedIn page.
        evidence: list[dict[str, Any]] = []
        rejected_linkedin = 0
        for record in data.get("emails", []) or []:
            sources = record.get("sources") or []

            usable = [
                s for s in sources
                if s.get("uri") and not (self.reject_linkedin and is_linkedin_derived(s))
            ]
            if sources and not usable:
                rejected_linkedin += 1
                continue

            uri = next((s.get("uri") for s in usable), None)
            if not uri:
                continue
            name = " ".join(
                p for p in (record.get("first_name"), record.get("last_name")) if p
            )
            evidence.append({
                "address": (record.get("value") or "").lower(),
                "name": name,
                "position": record.get("position") or "",
                "source_url": normalize_contact_url(uri),
            })

        if not evidence:
            # A pattern with no attributable source is not a pattern.
            if rejected_linkedin:
                log.warning(
                    "Hunter gave a pattern for %s, but all %s supporting address(es) "
                    "trace to LinkedIn. Rejecting under rule 3. No address will be "
                    "built for this firm.", domain, rejected_linkedin,
                )
            else:
                log.info("Hunter gave a pattern for %s but no sourced address to "
                         "back it. Rejecting.", domain)
            return None

        if rejected_linkedin:
            log.info("Dropped %s LinkedIn-derived address(es) for %s (rule 3).",
                     rejected_linkedin, domain)

        return {
            "pattern": f"{pattern}@{domain}",
            "confidence": round(min(0.9, 0.6 + 0.05 * len(evidence)), 2),
            "source_url": evidence[0]["source_url"],
            "derived_from": (
                f"Hunter domain-search, corroborated by {len(evidence)} address(es) "
                "attributed to public pages"
            ),
            "observed_examples": len(evidence),
            "provider": self.name,
            "evidence": evidence,
        }

    def _parse(self, email: str, payload: dict[str, Any]) -> VerificationResult:
        data = payload.get("data", {}) or {}
        status = str(data.get("result", "unknown")).lower()
        score = int(data.get("score") or 0)

        # An explicit undeliverable outranks whatever score came with it. Sending
        # to a known-bad address is what damages domain reputation.
        if status == "undeliverable":
            score = 0

        return VerificationResult(
            email=email, score=score, status=status, provider=self.name, raw=data
        )
