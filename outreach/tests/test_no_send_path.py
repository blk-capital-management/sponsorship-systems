"""Acceptance test 7: no code path calls a send endpoint.

Rule 2 is the one that cannot be walked back. If it ever breaks, it breaks by
someone adding a convenience "just send it" branch during a busy week, so this
test scans the whole package with the AST rather than trusting review.
"""

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent

# Modules whose mere import would transmit mail.
FORBIDDEN_IMPORTS = {"smtplib", "aiosmtplib", "yagmail", "sendgrid",
                     "mailjet_rest", "postmarker"}

# Gmail API method names that transmit. `drafts().create` is allowed, `send` is
# not. `drafts().send` is the one that looks harmless and is not.
FORBIDDEN_CALLS = {"send", "sendmail", "send_message", "sendMail"}

SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", "tests"}


def python_files() -> list[Path]:
    return [
        p for p in PROJECT_ROOT.rglob("*.py")
        if not any(part in SKIP_DIRS for part in p.parts)
    ]


def test_there_are_files_to_scan():
    """A scan that silently finds nothing would pass forever."""
    assert len(python_files()) >= 5


@pytest.mark.parametrize("path", python_files(), ids=lambda p: p.name)
def test_no_mail_transport_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {(node.module or "").split(".")[0]}
        else:
            continue
        offending = names & FORBIDDEN_IMPORTS
        assert not offending, (
            f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} imports {offending}. "
            "There is no code path that transmits email without human action (rule 2)."
        )


@pytest.mark.parametrize("path", python_files(), ids=lambda p: p.name)
def test_no_send_method_calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else None
        )
        # requests.Session.send exists but is not how this project fetches.
        assert name not in FORBIDDEN_CALLS, (
            f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} calls {name}(). "
            "The system produces Gmail drafts only. A human sends (rule 2)."
        )


def test_gmail_scope_cannot_send():
    """gmail.compose creates drafts. gmail.send would transmit, so it must be absent."""
    import yaml

    settings = yaml.safe_load(
        (PROJECT_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
    )
    scopes = settings["send"]["auth"]["scopes"]
    assert "https://www.googleapis.com/auth/gmail.compose" in scopes
    assert not any("gmail.send" in s for s in scopes)


def test_linkedin_is_blocked_at_the_request_layer():
    """Rule 3 is enforced in code, not only in documentation."""
    from common.http import BLOCKED_HOSTS

    assert "linkedin.com" in BLOCKED_HOSTS
