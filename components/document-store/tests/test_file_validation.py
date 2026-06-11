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
