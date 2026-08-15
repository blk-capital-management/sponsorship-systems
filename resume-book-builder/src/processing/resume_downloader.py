"""Download a single resume from Google Drive and return it as PDF bytes.

Orchestrates: file metadata fetch → MIME-based conversion → first-page extraction.
This is the main per-resume pipeline step called by the builders.

Lifted and unified from both legacy scripts' per-row download loops.
"""

from dataclasses import dataclass, field
from typing import Optional

from src.clients.drive_client import DriveClient, normalize_resume_reference
from src.processing.pdf_converter import convert_to_pdf, is_supported_mime, SUPPORTED_MIMES
from src.processing.first_page_extractor import extract_first_page
from src.utils.logging import get_logger

log = get_logger("processing.resume_downloader")


@dataclass
class DownloadResult:
    """Outcome of attempting to download + extract one resume."""
    success: bool
    pdf_bytes: Optional[bytes] = None
    file_id: Optional[str] = None
    mime_type: Optional[str] = None
    error_type: Optional[str] = None   # matches ErrorRecord.error_type values
    error_detail: Optional[str] = None


def download_resume(
    drive_client: DriveClient,
    resume_link: str,
    first_page_only: bool = True,
) -> DownloadResult:
    """Download a resume from a Google Drive link and optionally extract page 1.

    Args:
        drive_client: Authenticated DriveClient instance.
        resume_link: The Google Drive share URL from the spreadsheet.
        first_page_only: If True, return only the first page of the PDF.

    Returns:
        DownloadResult with success=True and pdf_bytes set, or success=False with
        error_type/error_detail describing what went wrong.
    """
    if not resume_link or not resume_link.strip():
        return DownloadResult(
            success=False,
            error_type="MISSING_RESUME_LINK",
            error_detail="Resume link is empty.",
        )

    file_id = normalize_resume_reference(resume_link)
    if not file_id:
        return DownloadResult(
            success=False,
            error_type="INVALID_DRIVE_LINK",
            error_detail=f"Could not extract a Drive file ID from: {resume_link}",
        )

    # Fetch metadata to determine MIME type
    try:
        meta = drive_client.get_file_metadata(file_id)
        mime_type = meta.get("mimeType", "")
        file_name = meta.get("name", file_id)
    except Exception as exc:
        error_msg = str(exc)
        if "403" in error_msg or "forbidden" in error_msg.lower():
            error_type = "BROKEN_PERMISSIONS"
            detail = f"Permission denied for file '{file_id}'. Ask student to share with 'Anyone with the link'."
        elif "404" in error_msg or "not found" in error_msg.lower():
            error_type = "BROKEN_PERMISSIONS"
            detail = f"File '{file_id}' not found or not accessible."
        else:
            error_type = "FAILED_DOWNLOAD"
            detail = f"Could not fetch metadata for file '{file_id}': {exc}"
        return DownloadResult(success=False, file_id=file_id, error_type=error_type, error_detail=detail)

    log.debug("Downloading '%s' (mime=%s)", file_name, mime_type)

    if not is_supported_mime(mime_type):
        return DownloadResult(
            success=False,
            file_id=file_id,
            mime_type=mime_type,
            error_type="UNSUPPORTED_FILE_TYPE",
            error_detail=(
                f"File '{file_name}' has unsupported type '{mime_type}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_MIMES))}"
            ),
        )

    # Convert to PDF
    pdf_bytes = convert_to_pdf(drive_client, file_id, mime_type)
    if not pdf_bytes:
        return DownloadResult(
            success=False,
            file_id=file_id,
            mime_type=mime_type,
            error_type="FAILED_DOWNLOAD",
            error_detail=f"Download/conversion returned empty bytes for '{file_name}'.",
        )

    # Optionally extract first page only
    if first_page_only:
        first_page = extract_first_page(pdf_bytes)
        if not first_page:
            return DownloadResult(
                success=False,
                file_id=file_id,
                mime_type=mime_type,
                error_type="FAILED_FIRST_PAGE_EXTRACTION",
                error_detail=f"Could not extract first page from '{file_name}'.",
            )
        pdf_bytes = first_page

    return DownloadResult(success=True, pdf_bytes=pdf_bytes, file_id=file_id, mime_type=mime_type)
