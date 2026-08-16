"""Config loading for the outreach pipeline.

Paths inside settings.yaml are relative to the config file's directory, matching
the contract used by resume-book-builder/config/settings.yaml.

Usage:
    from common.config import load_settings, load_blk_facts, resolve_path
    settings = load_settings()
    facts = load_blk_facts()
"""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

# Load .env once, at import, so every entry point sees the same secrets without
# each script remembering to do it. Values already in the environment win, which
# keeps CI and one-off overrides working.
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:  # pragma: no cover - python-dotenv is in requirements.txt
    pass


def resolve_path(value: str | os.PathLike, base_dir: Path | None = None) -> Path:
    """Resolve a config path against the config directory unless it is absolute."""
    p = Path(value)
    if p.is_absolute():
        return p
    return ((base_dir or CONFIG_DIR) / p).resolve()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=None)
def load_settings(path: str | None = None) -> dict[str, Any]:
    """Load settings.yaml."""
    settings_path = Path(path) if path else CONFIG_DIR / "settings.yaml"
    if not settings_path.exists():
        raise FileNotFoundError(f"Config file not found: {settings_path}")
    with settings_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache(maxsize=None)
def load_blk_facts(path: str | None = None) -> dict[str, Any]:
    """Load blk_facts.json. The only permitted source of BLK statistics (rule 5)."""
    return _read_json(Path(path) if path else CONFIG_DIR / "blk_facts.json")


@lru_cache(maxsize=None)
def load_compliance(path: str | None = None) -> dict[str, Any]:
    """Load compliance.json: mailing address, opt-out line, UK/EU basis."""
    return _read_json(Path(path) if path else CONFIG_DIR / "compliance.json")


@lru_cache(maxsize=None)
def load_email_patterns(path: str | None = None) -> dict[str, Any]:
    """Load email_patterns.json. Entries without a source_url are dropped here."""
    raw = _read_json(Path(path) if path else CONFIG_DIR / "email_patterns.json")
    patterns = raw.get("patterns", {})
    return {
        domain: entry
        for domain, entry in patterns.items()
        if isinstance(entry, dict) and entry.get("source_url")
    }


@lru_cache(maxsize=None)
def load_firm_categories(path: str | None = None) -> list[dict[str, Any]]:
    """Load firm_categories.json: the controlled vocabulary for firm_type."""
    raw = _read_json(Path(path) if path else CONFIG_DIR / "firm_categories.json")
    return [
        entry for entry in raw.get("categories", [])
        if isinstance(entry, dict) and entry.get("label")
    ]


@lru_cache(maxsize=None)
def _category_lookup(path: str | None = None) -> dict[str, str]:
    """Map every lowercased label and alias to its canonical label."""
    lookup: dict[str, str] = {}
    for entry in load_firm_categories(path):
        label = str(entry["label"])
        lookup[label.casefold()] = label
        for alias in entry.get("aliases") or []:
            lookup[str(alias).casefold()] = label
    return lookup


def normalize_firm_category(value: str, path: str | None = None) -> tuple[str, bool]:
    """Fold a firm_type onto the controlled vocabulary.

    Returns (value, recognized). An unrecognized value is returned stripped but
    otherwise verbatim with recognized=False, so intake can flag it without
    blocking. firm_type drives contact title routing, so a silent typo would
    quietly degrade targeting.
    """
    cleaned = " ".join(str(value or "").split())
    if not cleaned:
        return "", False
    canonical = _category_lookup(path).get(cleaned.casefold())
    return (canonical, True) if canonical else (cleaned, False)


def human_capital_first_types(path: str | None = None) -> list[str]:
    """Labels where Human Capital outranks campus-specific titles."""
    return [
        str(entry["label"]) for entry in load_firm_categories(path)
        if entry.get("human_capital_first")
    ]


def load_template(path: str | None = None) -> str:
    """Load template.md verbatim. Callers must not paraphrase it."""
    template_path = Path(path) if path else CONFIG_DIR / "template.md"
    if not template_path.exists():
        raise FileNotFoundError(f"Config file not found: {template_path}")
    return template_path.read_text(encoding="utf-8")


def get_api_key(env_var: str) -> str:
    """Read a secret from the environment. Keys never live in config files."""
    value = os.getenv(env_var)
    if not value:
        raise RuntimeError(
            f"{env_var} is not set. Add it to .env (see .env.example). "
            "Never commit a key."
        )
    return value
