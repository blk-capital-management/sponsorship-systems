"""Google Drive API client wrapper.

Provides:
- extract_file_id_from_link()  — parse Drive file ID from any share URL
- build_drive_service()        — authenticated Drive API service
- DriveClient                  — thin wrapper for download / export / upload operations

Lifted and cleaned up from legacy/custom_sponsor_resume_book_builder.py L99-L233.
"""

import io
import re
import tempfile
import os
from pathlib import Path
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

from src.utils.logging import get_logger

log = get_logger("clients.drive_client")

# Matches /d/<id>, id=<id>, open?id=<id>, uc?id=<id>, etc.
DRIVE_FILE_ID_RE = re.compile(r"(?:/d/|[?&]id=)([a-zA-Z0-9_-]{10,})")

# Bare file ID: 28–44 alphanumeric/dash/underscore chars with no URL structure.
_BARE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{28,44}$")


def extract_file_id_from_link(link: str) -> Optional[str]:
    """Return the Google Drive file ID from any share/view/download URL, or None."""
    if not link or not isinstance(link, str):
        return None
    match = DRIVE_FILE_ID_RE.search(link)
    return match.group(1) if match else None


def normalize_resume_reference(raw: str) -> Optional[str]:
    """Normalise any resume reference to a Google Drive file ID.

    Accepts:
      - Standard Drive share URL  (https://drive.google.com/file/d/ID/view)
      - Drive open/export URL     (https://drive.google.com/open?id=ID)
      - Google Forms file-upload  (https://drive.google.com/file/d/ID/…)
      - Raw Drive file ID         (28–44 alphanumeric chars, no slashes)

    Returns:
      The file ID string, or None if *raw* cannot be recognised.
    """
    if not raw or not isinstance(raw, str):
        return None

    value = raw.strip()

    # 1. Standard URL pattern covers all Drive and Forms-upload URLs
    url_match = DRIVE_FILE_ID_RE.search(value)
    if url_match:
        return url_match.group(1)

    # 2. Bare file ID (no URL structure)
    if _BARE_ID_RE.match(value):
        log.debug("normalize_resume_reference: treating '%s…' as bare file ID.", value[:12])
        return value

    log.warning(
        "normalize_resume_reference: could not extract a Drive file ID from: %s", value
    )
    return None


def build_drive_service(credentials):
    """Return an authenticated Drive API v3 service object."""
    return build("drive", "v3", credentials=credentials)


class DriveClient:
    """Thin wrapper around the Drive API for resume book operations."""

    def __init__(self, credentials):
        self.service = build_drive_service(credentials)

    def get_file_metadata(self, file_id: str) -> dict:
        """Fetch name and mimeType for a file."""
        return (
            self.service.files()
            .get(fileId=file_id, fields="id,name,mimeType")
            .execute()
        )

    def download_as_bytes(self, file_id: str) -> Optional[bytes]:
        """Download a native file (PDF, image, etc.) and return raw bytes, or None on failure."""
        try:
            request = self.service.files().get_media(fileId=file_id)
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            data = buf.getvalue()
            if not data:
                log.warning("Downloaded 0 bytes for file_id=%s", file_id)
                return None
            return data
        except Exception as exc:
            log.error("Failed to download file_id=%s: %s", file_id, exc)
            return None

    def export_as_pdf(self, file_id: str, mime_type: str) -> Optional[bytes]:
        """Export a Google Workspace file (Docs, Slides) to PDF bytes, or None on failure.

        mime_type should be the source mime type (e.g. application/vnd.google-apps.document).
        """
        try:
            request = self.service.files().export_media(
                fileId=file_id, mimeType="application/pdf"
            )
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            data = buf.getvalue()
            if not data:
                log.warning("Exported 0 bytes for Google file_id=%s (mime=%s)", file_id, mime_type)
                return None
            return data
        except Exception as exc:
            log.error("Failed to export file_id=%s (mime=%s) as PDF: %s", file_id, mime_type, exc)
            return None

    def upload_pdf(self, data: bytes, name: str, parent_folder_id: str) -> Optional[dict]:
        """Upload PDF bytes to Drive under parent_folder_id.

        Returns the created file metadata dict (id, webViewLink), or None on failure.
        """
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
                tf.write(data)
                tmp_path = tf.name

            media = MediaFileUpload(tmp_path, mimetype="application/pdf")
            body = {"name": name, "parents": [parent_folder_id]}
            file_meta = (
                self.service.files()
                .create(body=body, media_body=media, fields="id,webViewLink")
                .execute()
            )
            log.info("Uploaded '%s' → %s", name, file_meta.get("webViewLink", ""))
            return file_meta
        except Exception as exc:
            log.error("Failed to upload '%s': %s", name, exc)
            return None
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
