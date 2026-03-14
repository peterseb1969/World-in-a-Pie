"""Unit tests for ImportService (CSV/XLSX parsing, no DB needed)."""

import pytest
from document_store.services.import_service import ImportService


def _csv(headers: list[str], rows: list[list[str]]) -> bytes:
    """Build CSV bytes."""
    lines = [",".join(headers)]
    for row in rows:
        lines.append(",".join(row))
    return "\n".join(lines).encode("utf-8")


# =========================================================================
# detect_format
# =========================================================================


def test_detect_csv():
    assert ImportService.detect_format("data.csv") == "csv"


def test_detect_xlsx():
    assert ImportService.detect_format("data.xlsx") == "xlsx"


def test_detect_xls():
    assert ImportService.detect_format("report.xls") == "xls"


def test_detect_default():
    assert ImportService.detect_format("data.tsv") == "csv"


def test_detect_case_insensitive():
    assert ImportService.detect_format("DATA.XLSX") == "xlsx"


# =========================================================================
# preview
# =========================================================================


def test_preview_csv():
    content = _csv(["name", "age"], [["Alice", "30"], ["Bob", "25"]])
    result = ImportService.preview(content, "test.csv")
    assert result["format"] == "csv"
    assert result["headers"] == ["name", "age"]
    assert result["total_rows"] == 2
    assert len(result["sample_rows"]) == 2
    assert result["sample_rows"][0]["name"] == "Alice"


def test_preview_csv_with_bom():
    """CSV with UTF-8 BOM should still parse correctly."""
    content = b"\xef\xbb\xbf" + _csv(["name"], [["Alice"]])
    result = ImportService.preview(content, "bom.csv")
    assert result["headers"] == ["name"]
    assert result["total_rows"] == 1


def test_preview_csv_limits_sample():
    """Preview returns at most 5 sample rows."""
    rows = [[f"person_{i}"] for i in range(20)]
    content = _csv(["name"], rows)
    result = ImportService.preview(content, "big.csv")
    assert result["total_rows"] == 20
    assert len(result["sample_rows"]) == 5


def test_preview_empty_csv_raises():
    with pytest.raises(ValueError, match="no headers"):
        ImportService.preview(b"", "empty.csv")


def test_preview_xls_raises():
    """Legacy .xls format raises ValueError."""
    with pytest.raises(ValueError, match="not supported"):
        ImportService.preview(b"dummy", "old.xls")


# =========================================================================
# parse_rows
# =========================================================================


def test_parse_rows_csv():
    content = _csv(["a", "b"], [["1", "2"], ["3", "4"]])
    headers, rows = ImportService.parse_rows(content, "data.csv")
    assert headers == ["a", "b"]
    assert len(rows) == 2
    assert rows[0]["a"] == "1"
    assert rows[1]["b"] == "4"


# =========================================================================
# build_documents
# =========================================================================


def test_build_documents():
    rows = [
        {"Name": "Alice", "Email": "alice@test.com"},
        {"Name": "Bob", "Email": "bob@test.com"},
    ]
    mapping = {"Name": "name", "Email": "email"}
    docs = ImportService.build_documents(rows, "TPL-001", mapping, "wip")

    assert len(docs) == 2
    assert docs[0]["template_id"] == "TPL-001"
    assert docs[0]["namespace"] == "wip"
    assert docs[0]["data"]["name"] == "Alice"
    assert docs[0]["data"]["email"] == "alice@test.com"


def test_build_documents_skips_empty_values():
    rows = [{"Name": "Alice", "Email": ""}]
    mapping = {"Name": "name", "Email": "email"}
    docs = ImportService.build_documents(rows, "TPL-001", mapping, "wip")

    assert "email" not in docs[0]["data"]  # empty string skipped


def test_build_documents_unmapped_columns_ignored():
    rows = [{"Name": "Alice", "Extra": "ignored"}]
    mapping = {"Name": "name"}  # Extra not in mapping
    docs = ImportService.build_documents(rows, "TPL-001", mapping, "wip")

    assert "Extra" not in docs[0]["data"]
    assert "extra" not in docs[0]["data"]
    assert docs[0]["data"]["name"] == "Alice"
