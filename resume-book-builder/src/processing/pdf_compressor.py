"""PDF compression with Ghostscript preferred and pikepdf fallback.

Tier presets:
  high_quality  → Ghostscript /prepress  (300 dpi, full colour)
  email_quality → Ghostscript /ebook     (150 dpi, good balance)
  small_size    → Ghostscript /screen    (72 dpi, smallest file)

If Ghostscript is unavailable, pikepdf is attempted as a lossless fallback.
If neither is available the caller receives None and should use the uncompressed PDF.

Usage:
    from src.processing.pdf_compressor import compress_pdf
    result = compress_pdf(pdf_bytes, "email_quality")
    # result.bytes is the compressed PDF (or original if compression failed)
    # result.method is "ghostscript" | "pikepdf" | "none"
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.utils.logging import get_logger

log = get_logger("processing.pdf_compressor")

_GS_SETTINGS = {
    "high_quality": "/prepress",
    "email_quality": "/ebook",
    "small_size": "/screen",
}

VALID_TIERS = frozenset(_GS_SETTINGS)


@dataclass
class CompressionResult:
    bytes: Optional[bytes]          # compressed bytes, or None if completely failed
    method: str                     # "ghostscript" | "pikepdf" | "none"
    original_kb: float
    compressed_kb: float
    ratio_pct: float                # % reduction (positive = smaller)
    fallback_triggered: bool        # True when pikepdf was used instead of gs


def _find_ghostscript() -> Optional[str]:
    for candidate in ("gs", "gswin64c", "gswin32c"):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def _log_sizes(method: str, original_kb: float, compressed_kb: float) -> float:
    ratio = max(0.0, (1 - compressed_kb / original_kb) * 100) if original_kb else 0.0
    log.info(
        "Compression [%s]: %.1f KB → %.1f KB  (%.0f%% reduction)",
        method, original_kb, compressed_kb, ratio,
    )
    return ratio


def _compress_ghostscript(pdf_bytes: bytes, tier: str) -> Optional[bytes]:
    gs = _find_ghostscript()
    if not gs:
        return None

    pdf_settings = _GS_SETTINGS[tier]
    tmp_in = tmp_out = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f_in:
            f_in.write(pdf_bytes)
            tmp_in = f_in.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f_out:
            tmp_out = f_out.name

        cmd = [
            gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
            f"-dPDFSETTINGS={pdf_settings}",
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-sOutputFile={tmp_out}", tmp_in,
        ]
        log.debug("Running Ghostscript: %s", " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if proc.returncode != 0:
            log.error("Ghostscript failed (exit %d): %s", proc.returncode, proc.stderr.strip())
            return None

        data = Path(tmp_out).read_bytes()
        return data if data else None

    except subprocess.TimeoutExpired:
        log.error("Ghostscript timed out after 120 s.")
        return None
    except Exception as exc:
        log.error("Ghostscript error: %s", exc)
        return None
    finally:
        for tmp in (tmp_in, tmp_out):
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


def _compress_pikepdf(pdf_bytes: bytes) -> Optional[bytes]:
    try:
        import pikepdf
    except ImportError:
        log.warning("pikepdf not installed — cannot use Python fallback compression.")
        return None

    try:
        buf_in = io.BytesIO(pdf_bytes)
        buf_out = io.BytesIO()
        with pikepdf.open(buf_in) as pdf:
            pdf.save(buf_out, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
        data = buf_out.getvalue()
        return data if data else None
    except Exception as exc:
        log.error("pikepdf compression error: %s", exc)
        return None


def compress_pdf(pdf_bytes: bytes, tier: str = "email_quality") -> CompressionResult:
    """Compress pdf_bytes, trying Ghostscript first then pikepdf fallback.

    Always returns a CompressionResult; result.bytes is None only when both
    methods are unavailable (caller should use the original uncompressed PDF).
    """
    if tier not in VALID_TIERS:
        raise ValueError(f"Unknown tier '{tier}'. Choose from: {sorted(VALID_TIERS)}")

    original_kb = len(pdf_bytes) / 1024

    # ── Try Ghostscript ───────────────────────────────────────────────────────
    if _find_ghostscript():
        gs_bytes = _compress_ghostscript(pdf_bytes, tier)
        if gs_bytes:
            compressed_kb = len(gs_bytes) / 1024
            ratio = _log_sizes("ghostscript", original_kb, compressed_kb)
            return CompressionResult(
                bytes=gs_bytes, method="ghostscript",
                original_kb=original_kb, compressed_kb=compressed_kb,
                ratio_pct=ratio, fallback_triggered=False,
            )
        log.warning("Ghostscript available but compression failed; trying pikepdf fallback.")
    else:
        log.warning(
            "Ghostscript not found on PATH. Trying pikepdf fallback. "
            "For best quality install Ghostscript: brew install ghostscript (macOS) "
            "or apt install ghostscript (Linux)."
        )

    # ── Try pikepdf fallback ──────────────────────────────────────────────────
    pk_bytes = _compress_pikepdf(pdf_bytes)
    if pk_bytes:
        compressed_kb = len(pk_bytes) / 1024
        ratio = _log_sizes("pikepdf", original_kb, compressed_kb)
        log.warning(
            "COMPRESSION_FALLBACK: used pikepdf instead of Ghostscript. "
            "Install Ghostscript for better compression quality."
        )
        return CompressionResult(
            bytes=pk_bytes, method="pikepdf",
            original_kb=original_kb, compressed_kb=compressed_kb,
            ratio_pct=ratio, fallback_triggered=True,
        )

    # ── Both unavailable ──────────────────────────────────────────────────────
    log.warning(
        "No compression available (Ghostscript and pikepdf both unavailable). "
        "Build will continue with uncompressed PDF."
    )
    return CompressionResult(
        bytes=None, method="none",
        original_kb=original_kb, compressed_kb=original_kb,
        ratio_pct=0.0, fallback_triggered=False,
    )


# ── Backward-compat shim ──────────────────────────────────────────────────────

def compress_pdf_ghostscript(pdf_bytes: bytes, tier: str = "email_quality") -> Optional[bytes]:
    """Legacy entry point retained for backward compatibility and compress-only command.

    Prefers Ghostscript; falls back to pikepdf; returns None only when both fail.
    """
    result = compress_pdf(pdf_bytes, tier)
    return result.bytes
