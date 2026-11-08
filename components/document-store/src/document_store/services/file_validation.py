"""File upload validation — content type and filename sanitisation.

Security findings H5 (content-type validation) and L2 (filename sanitisation).
"""

import os
import re

from fastapi import HTTPException

# Default allowed MIME types. Override with WIP_ALLOWED_MIME_TYPES env var
# (comma-separated, e.g. "image/*,application/pdf,text/*"). A pattern ending
# in "*" matches every type that starts with what precedes it, so
# "image/*" admits image/png and "application/vnd.openxmlformats-officedocument.*"
# admits .docx, .xlsx and .pptx (and their templates). Macro-enabled Office
# documents (application/vnd.ms-word.document.macroEnabled.12 and kin) are
# deliberately NOT admitted: their types share neither prefix.
_DEFAULT_ALLOWED = (
    "image/*,"
    "audio/*,"
    "video/*,"
    "application/pdf,"
    "application/json,"
    "application/xml,"
    "text/*,"
    "application/vnd.openxmlformats-officedocument.*,"
    "application/msword,"
    "application/vnd.ms-excel,"
    "application/vnd.ms-powerpoint,"
    "application/zip,"
    "application/gzip,"
    "application/octet-stream"
)

# Dangerous file extensions that should always be rejected
BLOCKED_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".msi", ".scr", ".pif",
    ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".ps1",
    ".sh", ".csh", ".bash", ".dll", ".sys", ".drv",
}


def _get_allowed_types() -> list[str]:
    """Load allowed MIME types from environment or use defaults."""
    raw = os.getenv("WIP_ALLOWED_MIME_TYPES", _DEFAULT_ALLOWED)
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def content_type_matches(content_type: str, patterns: list[str]) -> bool:
    """Whether a MIME type matches any of ``patterns``.

    The one matching rule for the upload allowlist and a file field's
    ``allowed_types``: a pattern ending in ``*`` matches every type that
    starts with the text before the ``*`` ("*/*" and "*" match anything,
    "image/*" matches image/png, "application/vnd.openxmlformats-officedocument.*"
    matches .docx); any other pattern matches that type exactly. Case is
    ignored, and parameters ("; charset=utf-8") are not part of the type.
    """
    base_type = content_type.split(";")[0].strip().lower()
    for pattern in patterns:
        pattern = pattern.strip().lower()
        if pattern == "*/*":
            return True
        if pattern.endswith("*"):
            if base_type.startswith(pattern[:-1]):
                return True
        elif pattern == base_type:
            return True
    return False


def validate_upload_content_type(content_type: str, filename: str) -> None:
    """Validate that the upload's content type and extension are allowed.

    Raises HTTPException 415 if the type is not in the allowlist or the
    file extension is in the blocklist.
    """
    # Check blocked extensions
    _, ext = os.path.splitext(filename.lower())
    if ext in BLOCKED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"File extension '{ext}' is not allowed for security reasons",
        )

    # Check content type against allowlist
    allowed = _get_allowed_types()
    if not content_type_matches(content_type, allowed):
        raise HTTPException(
            status_code=415,
            detail=f"Content type '{content_type}' is not allowed. "
            f"Allowed types: {', '.join(allowed)}",
        )


def sanitize_filename(filename: str) -> str:
    """Sanitise a filename for safe storage and download headers (L2).

    Strips path separators, null bytes, control characters, and
    potentially dangerous sequences. Returns a safe filename that
    preserves the original extension.
    """
    if not filename:
        return "unnamed"

    # Strip directory components (Unix and Windows)
    filename = filename.replace("\\", "/")
    filename = filename.split("/")[-1]

    # Remove null bytes and control characters
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)

    # Remove characters problematic in HTTP headers and filesystems
    filename = re.sub(r'[<>:"|?*]', "_", filename)

    # Collapse multiple dots/underscores
    filename = re.sub(r"\.{2,}", ".", filename)
    filename = re.sub(r"_{2,}", "_", filename)

    # Strip leading/trailing dots and spaces
    filename = filename.strip(". ")

    if not filename:
        return "unnamed"

    return filename
