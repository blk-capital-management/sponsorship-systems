"""Tests for the PDF compression module (Ghostscript + pikepdf fallback)."""

import shutil
from unittest.mock import patch, MagicMock

import pytest

from src.processing.pdf_compressor import compress_pdf, compress_pdf_ghostscript, VALID_TIERS


FAKE_PDF = b"%PDF-1.4 1 0 obj<</Type/Catalog>>endobj\nxref\n0 2\n%%EOF"


def test_valid_tiers_constant():
    assert "high_quality" in VALID_TIERS
    assert "email_quality" in VALID_TIERS
    assert "small_size" in VALID_TIERS


def test_invalid_tier_raises():
    with pytest.raises(ValueError, match="Unknown tier"):
        compress_pdf(FAKE_PDF, "ultra_small")


def test_ghostscript_path_no_crash(monkeypatch):
    """compress_pdf does not crash even when Ghostscript is absent."""
    monkeypatch.setattr(shutil, "which", lambda x: None)
    result = compress_pdf(FAKE_PDF, "email_quality")
    # Falls through to pikepdf or returns method="none" — must not raise
    assert result.method in ("pikepdf", "none")
    assert result.original_kb > 0


def test_ghostscript_used_when_present(monkeypatch, tmp_path):
    """When Ghostscript is on PATH and succeeds, method='ghostscript'."""
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/gs" if x == "gs" else None)

    # Mock subprocess.run to simulate a successful gs run
    import subprocess

    fake_output = b"%PDF-1.4 compressed"

    def fake_run(cmd, **kwargs):
        # Write fake compressed output to the output file path
        out_path = None
        for arg in cmd:
            if arg.startswith("-sOutputFile="):
                out_path = arg.split("=", 1)[1]
        if out_path:
            with open(out_path, "wb") as f:
                f.write(fake_output)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = compress_pdf(FAKE_PDF, "email_quality")
    assert result.method == "ghostscript"
    assert result.bytes == fake_output
    assert not result.fallback_triggered


def test_pikepdf_fallback_when_gs_absent(monkeypatch):
    """When Ghostscript absent and pikepdf available, method='pikepdf'."""
    monkeypatch.setattr(shutil, "which", lambda x: None)

    fake_compressed = b"%PDF-1.4 pikepdf-compressed"

    import builtins
    real_import = builtins.__import__
    import io

    class FakePdf:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def save(self, buf, **kwargs): buf.write(fake_compressed)

    class FakePikepdf:
        ObjectStreamMode = MagicMock()
        ObjectStreamMode.generate = "generate"
        @staticmethod
        def open(buf): return FakePdf()

    def _fake_import(name, *args, **kwargs):
        if name == "pikepdf":
            return FakePikepdf()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    result = compress_pdf(FAKE_PDF, "email_quality")
    assert result.method == "pikepdf"
    assert result.fallback_triggered


def test_no_compression_available(monkeypatch):
    """When both Ghostscript and pikepdf are absent, method='none' and bytes=None."""
    monkeypatch.setattr(shutil, "which", lambda x: None)

    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "pikepdf":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    result = compress_pdf(FAKE_PDF, "email_quality")
    assert result.method == "none"
    assert result.bytes is None
    assert result.ratio_pct == 0.0


def test_compress_pdf_ghostscript_shim_returns_none_when_unavailable(monkeypatch):
    """Legacy shim returns None (not raises) when compression unavailable."""
    monkeypatch.setattr(shutil, "which", lambda x: None)

    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "pikepdf":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    result = compress_pdf_ghostscript(FAKE_PDF, "email_quality")
    assert result is None
