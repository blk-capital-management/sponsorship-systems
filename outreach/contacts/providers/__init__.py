"""Provider registry. Swapping backends is a config change, not a code change."""

import os

from common.logging import get_logger
from contacts.providers.base import (
    ProviderNotConfigured,
    VerificationProvider,
    VerificationResult,
)
from contacts.providers.hunter import HunterProvider
from contacts.providers.stubs import ApolloProvider, DryRunProvider, RocketReachProvider

log = get_logger("contacts.providers")

REGISTRY: dict[str, type[VerificationProvider]] = {
    "hunter": HunterProvider,
    "apollo": ApolloProvider,
    "rocketreach": RocketReachProvider,
    "dryrun": DryRunProvider,
}

__all__ = [
    "REGISTRY", "ProviderNotConfigured", "VerificationProvider",
    "VerificationResult", "build_provider",
]


def build_provider(settings: dict) -> VerificationProvider:
    """Construct the provider named in settings.yaml.

    The API key is read from the environment variable named in config. The key
    itself never appears in config, and is never logged.
    """
    cfg = settings["contacts"]["verification"]
    name = str(cfg.get("provider", "dryrun")).lower()

    if name not in REGISTRY:
        raise ProviderNotConfigured(
            f"Unknown verification provider {name!r}. "
            f"Choose one of: {', '.join(sorted(REGISTRY))}."
        )

    env_var = (cfg.get("api_key_env") or {}).get(name)
    api_key = os.getenv(env_var) if env_var else None

    log.debug("Verification provider: %s (key from %s: %s)",
              name, env_var, "set" if api_key else "not set")
    return REGISTRY[name](api_key=api_key)
