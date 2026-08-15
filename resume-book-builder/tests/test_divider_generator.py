"""Tests for programmatic divider page generation and the hybrid resolver."""

import pytest

from src.processing.divider_generator import generate_divider_page
from src.processing.pdf_merger import resolve_divider_for_year


def test_generate_divider_returns_pdf_bytes():
    """generate_divider_page should return non-empty bytes starting with %PDF."""
    try:
        import reportlab  # noqa: F401
    except ImportError:
        pytest.skip("reportlab not installed")
    data = generate_divider_page(2026)
    assert data is not None
    assert len(data) > 0
    assert data[:4] == b"%PDF"


def test_generate_divider_different_years():
    try:
        import reportlab  # noqa: F401
    except ImportError:
        pytest.skip("reportlab not installed")
    data_2025 = generate_divider_page(2025)
    data_2028 = generate_divider_page(2028)
    assert data_2025 is not None
    assert data_2028 is not None
    # Different years produce different PDFs (year is embedded in content)
    assert data_2025 != data_2028


def test_generate_divider_without_reportlab(monkeypatch):
    """generate_divider_page returns None gracefully when reportlab is missing."""
    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "reportlab" or name.startswith("reportlab."):
            raise ImportError("reportlab not available")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    result = generate_divider_page(2026)
    assert result is None


# ── Hybrid resolver tests ─────────────────────────────────────────────────────

def test_resolve_uses_canva_when_present():
    """Manual Canva PDF takes priority over auto-generation."""
    canva_bytes = b"%PDF-1.4 fake canva content for 2026"
    transitions = {"2026 transition": canva_bytes}
    divider, source = resolve_divider_for_year(transitions, 2026)
    assert source == "canva"
    assert divider == canva_bytes


def test_resolve_generates_when_canva_missing():
    """Auto-generation kicks in when no Canva PDF exists for the year."""
    try:
        import reportlab  # noqa: F401
    except ImportError:
        pytest.skip("reportlab not installed")
    transitions = {}  # no Canva PDFs
    divider, source = resolve_divider_for_year(transitions, 2027)
    assert source == "generated"
    assert divider is not None
    assert divider[:4] == b"%PDF"


def test_resolve_missing_when_no_reportlab(monkeypatch):
    """Returns ('missing', None) when Canva absent and reportlab unavailable."""
    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "reportlab" or name.startswith("reportlab."):
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    divider, source = resolve_divider_for_year({}, 2025)
    assert source == "missing"
    assert divider is None


def test_resolve_canva_priority_over_generation(monkeypatch):
    """Canva PDF returned even when reportlab is available."""
    canva = b"%PDF-fake-2025"
    transitions = {"2025 transition": canva}
    divider, source = resolve_divider_for_year(transitions, 2025)
    assert source == "canva"
    assert divider == canva
