"""A number a document cannot store is refused on the way in, wherever it sits.

MongoDB holds a 64-bit integer at most. A larger whole number used to be
refused only in a scalar integer or number field; in an array item, a
duration's value, an untyped value or the free-form metadata it passed
validation, the Registry allocated the document's id, and the insert failed
at encoding, failing every document written in the same insert with it.
"""

import pytest
from httpx import AsyncClient

from document_store.models.api_models import DocumentCreateRequest, PatchDocumentItem
from document_store.services.import_service import CellConversionError, convert_cell
from document_store.services.validation_service import ValidationResult, ValidationService
from tests.test_files import auth_headers, file_client  # noqa: F401 — fixtures

TOO_BIG = 2**63
TOO_SMALL = -(2**63) - 1


@pytest.mark.parametrize(("data", "path", "code"), [
    ({"scores": [1, TOO_BIG]}, "scores[1]", "maximum"),
    ({"timeout": {"value": TOO_BIG, "unit": "seconds"}}, "timeout.value", "maximum"),
    ({"extra": {"nested": [{"n": TOO_SMALL}]}}, "extra.nested[0].n", "minimum"),
    ({"age": TOO_BIG}, "age", "maximum"),
])
def test_stage_one_refuses_a_whole_number_outside_64_bits_anywhere(data, path, code):
    result = ValidationResult()
    assert not ValidationService()._validate_structural(data, result)
    [error] = result.errors
    assert error["field"] == path and error["code"] == code, error
    assert "2^63" in error["message"], error


def test_stage_one_passes_the_bounds_and_booleans():
    result = ValidationResult()
    data = {"a": [2**63 - 1, -(2**63)], "b": True, "c": {"d": 1.5}}
    assert ValidationService()._validate_structural(data, result)
    assert result.errors == []


@pytest.mark.asyncio
async def test_an_array_item_outside_64_bits_is_a_validation_failure(client: AsyncClient, auth_headers):  # noqa: F811
    """Through the write path: the item is refused at validation, before any
    id is allocated, with the item's path."""
    resp = await client.post("/api/document-store/documents", headers=auth_headers, json=[{
        "template_id": "PERSON", "namespace": "wip",
        "data": {"national_id": "128200001", "first_name": "A", "last_name": "B",
                 "gender": "M", "lucky_numbers": [7, TOO_BIG]},
    }])
    assert resp.status_code == 200, resp.text
    [item] = resp.json()["results"]
    assert item["status"] == "error" and item["error_code"] == "validation_failed", item
    assert "lucky_numbers[1]" in item["error"], item


@pytest.mark.parametrize("value", [TOO_BIG, TOO_SMALL, float("nan"), float("inf")])
def test_document_metadata_with_an_unstorable_number_is_refused(value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="cannot be stored"):
        DocumentCreateRequest(template_id="T", namespace="wip", data={}, metadata={"n": [value]})
    with pytest.raises(ValidationError, match="cannot be stored"):
        PatchDocumentItem(document_id="d", patch={}, metadata_patch={"n": {"m": value}})


def test_storable_metadata_is_kept():
    req = DocumentCreateRequest(
        template_id="T", namespace="wip", data={}, metadata={"n": 2**63 - 1, "f": True},
    )
    assert req.metadata == {"n": 2**63 - 1, "f": True}


@pytest.mark.asyncio
async def test_a_create_with_unstorable_metadata_is_refused(client: AsyncClient, auth_headers):  # noqa: F811
    resp = await client.post("/api/document-store/documents", headers=auth_headers, json=[{
        "template_id": "PERSON", "namespace": "wip",
        "data": {"national_id": "128200002", "first_name": "A", "last_name": "B", "gender": "M"},
        "metadata": {"source_row": TOO_BIG},
    }])
    assert resp.status_code == 422, resp.text
    assert "metadata" in resp.text and "source_row" in resp.text


@pytest.mark.asyncio
async def test_a_file_whose_custom_metadata_cannot_be_stored_is_refused(file_client, auth_headers):  # noqa: F811
    resp = await file_client.post(
        "/api/document-store/files", headers=auth_headers,
        files={"file": ("n.txt", b"numbers", "text/plain")},
        data={"namespace": "wip", "custom": '{"n": 9223372036854775808}'},
    )
    assert resp.status_code == 422, resp.text
    assert "cannot be stored" in resp.text

    resp = await file_client.patch("/api/document-store/files", headers=auth_headers, json=[
        {"file_id": "f", "custom": {"n": TOO_BIG}},
    ])
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("field_type", ["integer", "number"])
def test_a_numeral_longer_than_pythons_digit_limit_fails_its_row(field_type):
    """int() refuses more than 4300 digits with a plain ValueError, which used
    to escape the row and end the whole import as a bare 500."""
    with pytest.raises(CellConversionError, match="too long"):
        convert_cell("1" * 4301, field_type)


@pytest.mark.asyncio
async def test_an_import_with_an_overlong_numeral_fails_only_that_row(client: AsyncClient, auth_headers):  # noqa: F811
    from tests.test_import_typed import HEADERS, _csv, _import

    body = await _import(client, auth_headers, "long.csv", _csv(HEADERS, [
        ["128200003", "Ada", "Lovelace", "1" * 4301, "1815-12-10"],
        ["128200004", "Grace", "Hopper", "85", "1906-12-09"],
    ]))
    assert body["succeeded"] == 1 and body["failed"] == 1, body
    [error] = body["errors"]
    assert error["row"] == 2 and "too long" in error["error"], error
