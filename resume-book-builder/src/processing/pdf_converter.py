"""Convert non-PDF files (Google Docs, Word .docx) to PDF bytes.

The Drive API handles the conversion for Google Workspace files.
Word documents are exported via the Drive API's export endpoint (Drive converts them
server-side when they were uploaded as .docx).

Lifted and generalised from legacy/custom_sponsor_resume_book_builder.py L110-L190.
"""

from typing import Optional

from src.clients.drive_client import DriveClient
from src.utils.logging import get_logger

log = get_logger("processing.pdf_converter")

# MIME types that the Drive API can export as PDF
GOOGLE_DOC_MIMES = {
    "application/vnd.google-apps.document",       # Google Docs
    "application/vnd.google-apps.presentation",   # Google Slides
    "application/vnd.google-apps.spreadsheet",    # Google Sheets (edge case)
}

# MIME types that Drive will convert when they were uploaded (Word docs stored as-is)
WORD_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/msword",                                                         # .doc
}

SUPPORTED_MIMES = {"application/pdf"} | GOOGLE_DOC_MIMES | WORD_MIMES


def is_supported_mime(mime_type: str) -> bool:
    return mime_type in SUPPORTED_MIMES


def convert_to_pdf(drive_client: DriveClient, file_id: str, mime_type: str) -> Optional[bytes]:
    """Return PDF bytes for any supported Drive file, or None on failure.

    - Native PDFs: downloaded directly.
    - Google Docs / Slides: exported to PDF via Drive API.
    - Word .docx/.doc stored in Drive: exported to PDF via Drive API.

    Args:
        drive_client: Authenticated DriveClient instance.
        file_id: Drive file ID.
        mime_type: MIME type of the source file (from Drive metadata).

    Returns:
        PDF bytes, or None if the file type is unsupported or conversion failed.
    """
    if mime_type == "application/pdf":
        log.debug("Downloading native PDF file_id=%s", file_id)
        return drive_client.download_as_bytes(file_id)

    if mime_type in GOOGLE_DOC_MIMES:
        log.debug("Exporting Google Doc/Slides file_id=%s to PDF", file_id)
        return drive_client.export_as_pdf(file_id, mime_type)

    if mime_type in WORD_MIMES:
        log.debug("Exporting Word document file_id=%s to PDF via Drive", file_id)
        return drive_client.export_as_pdf(file_id, mime_type)

    log.warning(
        "Unsupported MIME type '%s' for file_id=%s. Cannot convert to PDF.",
        mime_type, file_id,
    )
    return None
