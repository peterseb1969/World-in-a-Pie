"""upload_file declares an Office Open XML file under its own type.

The tool took the type from mimetypes.guess_type, whose built-in table lacks
.docx, .xlsx and .pptx: they come from the system's mime.types, which the
server's slim image does not have. Such a file was declared
application/octet-stream, and a file field allowing only Office types
refused it.
"""

from unittest.mock import patch

import pytest

from wip_mcp.server import _upload_content_type


@pytest.mark.parametrize(("name", "expected"), [
    ("report.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("Sheet.XLSX", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("deck.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
])
def test_ooxml_types_without_a_system_table(name, expected):
    import mimetypes

    with patch.object(mimetypes, "guess_type", return_value=(None, None)):
        assert _upload_content_type(name) == expected


def test_other_files_keep_the_guess_and_its_fallback():
    assert _upload_content_type("scan.pdf") == "application/pdf"
    assert _upload_content_type("blob.unknownext") == "application/octet-stream"
