"""Google authentication — supports OAuth user flow (default) and service account.

Auth mode is controlled by settings.yaml:
  auth.mode: oauth            — interactive browser login, token cached in token.json
  auth.mode: service_account  — headless, reads GOOGLE_SERVICE_ACCOUNT_FILE from env

Usage:
    from src.auth.google_auth import build_credentials
    creds = build_credentials(settings)
"""

import os
from pathlib import Path
from typing import Any, Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from src.utils.file_utils import resolve_path
from src.utils.logging import get_logger

log = get_logger("auth.google_auth")

# Populated lazily so google-auth-httplib2 import doesn't fail when service account
# is not configured.
_ServiceAccountCredentials = None


def _import_service_account():
    global _ServiceAccountCredentials
    if _ServiceAccountCredentials is None:
        from google.oauth2.service_account import Credentials as SACredentials
        _ServiceAccountCredentials = SACredentials
    return _ServiceAccountCredentials


def build_credentials(settings: dict[str, Any], config_dir: Optional[Path] = None) -> Any:
    """Return authenticated Google credentials based on settings.yaml auth.mode.

    Args:
        settings: Parsed settings.yaml dict (top-level keys: auth, ...).
        config_dir: Directory containing settings.yaml. Relative credentials_file/
                    token_file paths are resolved against this directory, matching
                    every other path in settings.yaml. Falls back to the current
                    working directory if not provided.

    Returns:
        google.oauth2 credentials object ready to pass to googleapiclient.discovery.build().

    Raises:
        FileNotFoundError: credentials file not found.
        ValueError: unknown auth mode.
        SystemExit: user cancelled OAuth flow.
    """
    auth_cfg = settings.get("auth", {})
    mode = auth_cfg.get("mode", "oauth").lower()
    scopes = auth_cfg.get("scopes", [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/spreadsheets.readonly",
    ])
    base_dir = config_dir or Path.cwd()

    if mode == "oauth":
        return _oauth_credentials(auth_cfg, scopes, base_dir)
    elif mode == "service_account":
        return _service_account_credentials(scopes)
    else:
        raise ValueError(f"Unknown auth.mode '{mode}'. Must be 'oauth' or 'service_account'.")


# ── OAuth flow ────────────────────────────────────────────────────────────────

def _oauth_credentials(auth_cfg: dict, scopes: list[str], base_dir: Path) -> Credentials:
    credentials_file = _resolve_credentials_path(auth_cfg, "GOOGLE_CREDENTIALS_FILE", "credentials_file", "credentials.json", base_dir)
    token_file = _resolve_credentials_path(auth_cfg, "GOOGLE_TOKEN_FILE", "token_file", "token.json", base_dir)

    creds: Credentials | None = None

    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), scopes)
            log.debug("Loaded cached token from %s", token_file)
        except Exception as exc:
            log.warning("Could not load token file (%s) — will re-authenticate.", exc)
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        log.info("Refreshing expired Google token…")
        creds.refresh(Request())
    else:
        if not credentials_file.exists():
            raise FileNotFoundError(
                f"Google credentials file not found: {credentials_file}\n"
                "Download it from Google Cloud Console → Credentials → OAuth 2.0 Client IDs."
            )
        log.info("Starting Google OAuth login (browser window will open)…")
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), scopes)
        creds = flow.run_local_server(port=0)
        log.info("OAuth login successful.")

    # Cache the token
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    log.debug("Token cached at %s", token_file)

    return creds


# ── Service account flow ──────────────────────────────────────────────────────

def _service_account_credentials(scopes: list[str]) -> Any:
    SACredentials = _import_service_account()

    sa_file_env = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not sa_file_env:
        raise ValueError(
            "auth.mode is 'service_account' but GOOGLE_SERVICE_ACCOUNT_FILE is not set in .env. "
            "Add the path to your service account JSON key."
        )
    sa_path = Path(sa_file_env)
    if not sa_path.exists():
        raise FileNotFoundError(f"Service account key not found: {sa_path}")

    log.info("Authenticating with service account: %s", sa_path.name)
    creds = SACredentials.from_service_account_file(str(sa_path), scopes=scopes)
    return creds


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_credentials_path(
    auth_cfg: dict, env_var: str, cfg_key: str, default: str, base_dir: Path,
) -> Path:
    """Resolve a credential file path from env var, then settings, then default.

    Relative paths are resolved against base_dir (the settings.yaml directory),
    matching the "Paths are relative to this config file's directory" contract
    documented in settings.yaml. Absolute paths and env var overrides pass through.
    """
    env_val = os.getenv(env_var)
    if env_val:
        return Path(env_val) if Path(env_val).is_absolute() else resolve_path(env_val, base_dir)
    cfg_val = auth_cfg.get(cfg_key)
    if cfg_val:
        return resolve_path(cfg_val, base_dir)
    return resolve_path(default, base_dir)
