"""Apollo, RocketReach, and dry-run adapters.

Apollo and RocketReach conform to the same interface as Hunter but are not
implemented, because implementing an API this project has no key for produces
code that has never run. Each raises a clear message naming what to do.

The dry-run provider is the useful one before a key exists: it exercises the
whole pipeline, scores nothing, and forces every address to be treated as
unverified so nothing can slip into the queue looking checked.
"""

from common.logging import get_logger
from contacts.providers.base import (
    ProviderNotConfigured,
    VerificationProvider,
    VerificationResult,
)

log = get_logger("contacts.providers.stubs")


class ApolloProvider(VerificationProvider):
    name = "apollo"

    def verify(self, email: str) -> VerificationResult:
        raise ProviderNotConfigured(
            "The Apollo adapter is a stub. Implement verify() against "
            "https://apolloio.github.io/apollo-api-docs/ and set APOLLO_API_KEY, "
            "or set contacts.verification.provider to 'hunter'."
        )


class RocketReachProvider(VerificationProvider):
    name = "rocketreach"

    def verify(self, email: str) -> VerificationResult:
        raise ProviderNotConfigured(
            "The RocketReach adapter is a stub. Implement verify() against "
            "https://rocketreach.co/api and set ROCKETREACH_API_KEY, or set "
            "contacts.verification.provider to 'hunter'."
        )


class DryRunProvider(VerificationProvider):
    """Scores nothing. Every address comes back unverified and gets dropped.

    This is deliberately not a pass-through. A dry run that marked addresses
    deliverable would put unverified addresses in front of a human who has been
    told the queue only contains verified ones.
    """

    name = "dryrun"

    def __init__(self, api_key: str | None = None, timeout: int = 20):
        super().__init__(api_key, timeout)
        log.warning(
            "Verification provider is 'dryrun'. No address will pass the "
            "deliverability threshold. Set a real provider before sending."
        )

    def verify(self, email: str) -> VerificationResult:
        return VerificationResult(
            email=email, score=0, status="unverified", provider=self.name,
            raw={"note": "dry run, no provider called"},
        )
