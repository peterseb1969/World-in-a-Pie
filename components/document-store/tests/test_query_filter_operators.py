"""A query filter's field and value cannot carry MongoDB operators.

POST /documents/query put `QueryFilter.field` straight into the MongoDB query
as a key, and the value straight in as its operand. A field of `$where` ran
the value as server-side JavaScript on the MongoDB all of WIP shares; `$expr`
ran arbitrary per-document computation; a value of `{"$regex": ...}` on an
`eq` filter applied an operator around the operator allowlist. Field paths
stay free — any `data.<path>`, declared or not, in any spelling a template
field name allows — but no path segment may start with `$`, and no key
anywhere in the value may.
"""

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from document_store.models.api_models import DocumentQueryRequest, QueryFilter
from document_store.services.document_service import DocumentService

QUERY = "/api/document-store/documents/query"


async def _create(client: AsyncClient, auth_headers: dict, data: dict) -> None:
    response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[{"namespace": "wip", "template_id": "PERSON", "data": data}],
    )
    assert response.status_code == 200 and response.json()["succeeded"] == 1, response.text


async def _query(client: AsyncClient, auth_headers: dict, *filters: dict):
    return await client.post(
        QUERY,
        headers=auth_headers,
        json={"filters": list(filters), "template_id": "PERSON"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["$where", "$expr", "$or", "data.$where", "data.address.$ne"])
async def test_an_operator_in_the_field_is_refused(client, auth_headers, field):
    await _create(client, auth_headers, {"national_id": "713400001", "first_name": "Wherey", "last_name": "Q"})
    response = await _query(
        client, auth_headers, {"field": field, "operator": "eq", "value": "this.data.first_name == 'Wherey'"}
    )
    assert response.status_code == 422, response.text
    assert "start with '$'" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["data.a\u0000b", "data..name", "data.", ".data"])
async def test_a_path_no_document_can_hold_is_refused(client, auth_headers, field):
    """A NUL byte cannot be a BSON key: the encoder used to fail inside the
    query and the caller got a 500. Empty segments name nothing. Both are
    refused like an operator segment."""
    response = await _query(client, auth_headers, {"field": field, "operator": "eq", "value": 1})
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_server_side_javascript_cannot_be_reached(client, auth_headers):
    """`$where` alone fails in the route's count (an aggregation `$match`),
    but `$expr` with `$function` runs JavaScript there; the field is refused
    before either reaches MongoDB."""
    await _create(client, auth_headers, {"national_id": "713400003", "first_name": "Js", "last_name": "Fn"})
    js = {"$function": {"body": "function() { return true; }", "args": [], "lang": "js"}}
    response = await _query(client, auth_headers, {"field": "$expr", "operator": "eq", "value": js})
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operator", "value"),
    [
        ("eq", {"$regex": "^W"}),
        ("ne", {"$type": "string"}),
        ("gt", {"$where": "true"}),
        ("in", ["Wherey", {"$gt": ""}]),
        ("nin", [{"nested": {"$ne": 1}}]),
        ("eq", {"city": {"$exists": True}}),
    ],
)
async def test_an_operator_in_the_value_is_refused(client, auth_headers, operator, value):
    response = await _query(
        client, auth_headers, {"field": "data.first_name", "operator": operator, "value": value}
    )
    assert response.status_code == 422, response.text
    assert "may not carry MongoDB operators" in response.text


@pytest.mark.asyncio
async def test_ordinary_paths_and_plain_object_values_still_filter(client, auth_headers):
    await _create(client, auth_headers, {"national_id": "713400002", "first_name": "Plain", "last_name": "Path"})
    ok = await _query(client, auth_headers, {"field": "data.first_name", "operator": "eq", "value": "Plain"})
    assert ok.status_code == 200, ok.text
    assert [d["data"]["first_name"] for d in ok.json()["items"]] == ["Plain"]

    # Template field names are free strings, so paths outside a strict
    # identifier pattern stay filterable; a plain object still matches a
    # sub-document exactly; a '$' that does not start a segment is data.
    for f in (
        {"field": "data.first-name", "operator": "eq", "value": "x"},
        {"field": "data.größe", "operator": "exists", "value": True},
        {"field": "data.price$usd", "operator": "gte", "value": 1},
        {"field": "data.address", "operator": "eq", "value": {"city": "Berlin"}},
    ):
        response = await _query(client, auth_headers, f)
        assert response.status_code == 200, (f, response.text)


def test_the_builder_refuses_an_unvalidated_operator_filter():
    """A filter built without validation still cannot reach the query."""
    for bad in (
        QueryFilter.model_construct(field="$where", operator="eq", value="true"),
        QueryFilter.model_construct(field="data.x", operator="eq", value={"$regex": "a"}),
    ):
        request = DocumentQueryRequest.model_construct(
            filters=[bad], template_id=None, status=None
        )
        with pytest.raises(ValueError, match="MongoDB operator"):
            DocumentService()._build_query(request)


def test_the_model_refuses_operators_at_construction():
    with pytest.raises(ValidationError):
        QueryFilter(field="$where", operator="eq", value="true")
    with pytest.raises(ValidationError):
        QueryFilter(field="data.x", operator="in", value=[{"$gt": 0}])
    with pytest.raises(ValidationError):
        QueryFilter(field="", operator="eq", value=1)
