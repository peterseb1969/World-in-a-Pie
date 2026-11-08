"""Unit tests for upload content-type validation (CASE-448).

First coverage of the H5 allowlist: media classes admitted by default,
disallowed types still rejected, env override honored, extension blocklist
independent of content type.
"""

import pytest
from fastapi import HTTPException

from document_store.services.file_validation import (
    validate_upload_content_type,
)


@pytest.mark.parametrize(
    "content_type,filename",
    [
        ("audio/wav", "take1.wav"),
        ("audio/mpeg", "song.mp3"),
        ("video/webm", "clip.webm"),
        ("video/mp4", "clip.mp4"),
        ("image/png", "pic.png"),
        ("application/pdf", "doc.pdf"),
        ("application/octet-stream", "blob.bin"),
        # Parameters after the base type must not break matching
        ("audio/wav; rate=44100", "take2.wav"),
    ],
)
def test_default_allowlist_admits(content_type, filename):
    validate_upload_content_type(content_type, filename)  # must not raise


@pytest.mark.parametrize(
    "content_type",
    [
        "application/x-msdownload",
        "application/x-sh",
        "font/woff2",
    ],
)
def test_default_allowlist_rejects(content_type):
    with pytest.raises(HTTPException) as exc:
        validate_upload_content_type(content_type, "payload.dat")
    assert exc.value.status_code == 415


def test_blocked_extension_rejected_regardless_of_type():
    with pytest.raises(HTTPException) as exc:
        validate_upload_content_type("text/plain", "script.sh")
    assert exc.value.status_code == 415


def test_env_override_replaces_default(monkeypatch):
    monkeypatch.setenv("WIP_ALLOWED_MIME_TYPES", "application/pdf")
    with pytest.raises(HTTPException):
        validate_upload_content_type("audio/wav", "take1.wav")
    validate_upload_content_type("application/pdf", "doc.pdf")


# ── Office documents (CASE-1156) ──────────────────────────────────────────
# The default list named the OOXML family as "...officedocument.*", which the
# matcher read as an exact type, and legacy Word as "application/vnd.ms-word",
# which is not a registered type (.doc is application/msword) — so every Word,
# Excel and PowerPoint upload was refused except legacy .xls.

OFFICE = [
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "a.docx"),
    ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "a.xlsx"),
    ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "a.pptx"),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.template", "a.dotx"),
    ("application/msword", "a.doc"),
    ("application/vnd.ms-excel", "a.xls"),
    ("application/vnd.ms-powerpoint", "a.ppt"),
]


@pytest.mark.parametrize("content_type,filename", OFFICE)
def test_default_allowlist_admits_office_documents(content_type, filename):
    validate_upload_content_type(content_type, filename)  # must not raise


@pytest.mark.parametrize(
    "content_type",
    [
        "application/vnd.ms-word.document.macroEnabled.12",
        "application/vnd.ms-excel.sheet.macroEnabled.12",
        "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
    ],
)
def test_default_allowlist_refuses_macro_enabled_office_documents(content_type):
    """A deliberate line, not an accident of the pattern: macro-enabled types
    share neither the OOXML prefix nor an exact legacy entry."""
    with pytest.raises(HTTPException) as exc:
        validate_upload_content_type(content_type, "a.docm")
    assert exc.value.status_code == 415


@pytest.mark.parametrize(
    "pattern,content_type,expected",
    [
        ("application/vnd.openxmlformats-officedocument.*",
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", True),
        ("application/vnd.openxmlformats-officedocument.*", "application/vnd.ms-excel", False),
        ("image/*", "image/png", True),
        ("image/*", "imagex/png", False),
        ("*/*", "anything/at-all", True),
        ("*", "anything/at-all", True),
        ("text/csv", "text/csv; charset=utf-8", True),
        ("TEXT/CSV", "text/csv", True),
        ("text/csv", "text/csvx", False),
    ],
)
def test_one_matching_rule(pattern, content_type, expected):
    """The rule shared by the upload allowlist and a file field's allowed_types."""
    from document_store.services.file_validation import content_type_matches

    assert content_type_matches(content_type, [pattern]) is expected
