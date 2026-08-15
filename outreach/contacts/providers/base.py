"""Provider-agnostic email verification interface.

One interface, several backends, selected by config so the provider can be
swapped without touching pipeline code:

    contacts:
      verification:
        provider: hunter        # hunter | apollo | rocketreach | dryrun

API keys are read from environment variables named in settings.yaml. No key is
ever written to a config file or committed.

Adding a provider means subclassing VerificationProvider and registering it in
contacts/providers/__init__.py. Nothing else changes.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class VerificationResult:
    """One address's verification outcome, normalized across providers.

    `score` is 0-100 regardless of what the provider natively returns, so the
    deliverability threshold in settings.yaml means the same thing whichever
    backend is configured.
    """

    email: str
    score: int
    status: str
    provider: str
    raw: dict[str, Any] | None = None
    error: str | None = None

    @property
    def usable(self) -> bool:
        return self.error is None and self.score > 0


class VerificationProvider(ABC):
    """Base class for every verification backend."""

    name: str = "base"

    def __init__(self, api_key: str | None = None, timeout: int = 20):
        self.api_key = api_key
        self.timeout = timeout

    @abstractmethod
    def verify(self, email: str) -> VerificationResult:
        """Verify a single address and return a normalized result."""

    def verify_many(self, emails: list[str]) -> list[VerificationResult]:
        """Verify several addresses. Overridden where a provider has a bulk API."""
        return [self.verify(email) for email in emails]

    def discover_pattern(self, domain: str) -> dict[str, Any] | None:
        """Return this domain's email pattern from the provider's licensed corpus.

        Most firms publish no personal addresses on their own website, so
        site-only inference returns nothing for them. A licensed provider API is
        the permitted second source (rule 3 forbids LinkedIn, not licensed APIs).

        Implementations must return an entry carrying a real `source_url`
        pointing at the public page the provider extracted the address from. A
        provider that cannot supply a source URL must return None, because a
        pattern with no source is not a pattern.

        Returns None by default so providers opt in.
        """
        return None


class ProviderNotConfigured(RuntimeError):
    """Raised when the selected provider has no usable credentials."""
