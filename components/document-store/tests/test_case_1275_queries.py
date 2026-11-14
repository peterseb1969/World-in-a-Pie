"""A caller's namespace condition survives the permission filter.

Provenance: CASE-1275 (0ba6bfb2 R4, 65286754 R1/R2).

`query_documents` built its query and then merged the caller's readable-namespace
clause with `dict.update`, which replaced a `namespace` the query already
constrained: a query filtering on one namespace was answered with rows from every
namespace the key can read. The clause is now ANDed, as two filters on any other
field already were.

The table routes resolved the template in the `namespace` they were given before
deciding whether the caller may read it; read is now decided first, and a
namespace the caller cannot read is refused the way `GET /documents` refuses it.
"""

import pytest
from httpx import AsyncClient

from document_store.services.namespace_scope import and_namespace

API = "/api/document-store"
BOTH_READER = {"X-API-Key": "test_xns_reader_key"}  # reads 'wip' and 'xns-closed'
WIP_READER = {"X-API-Key": "test_xns_writer_key"}  # reads 'wip' only


def test_a_namespace_condition_joins_rather_than_replaces():
    joined = and_namespace({"namespace": "wip"}, {"namespace": {"$in": ["wip", "xns-closed"]}})
    assert joined["namespace"] == "wip"
    assert joined["$and"] == [{"namespace": {"$in": ["wip", "xns-closed"]}}]

    # Nothing on the key yet: the clause takes it directly.
    assert and_namespace({}, {"namespace": {"$in": ["wip"]}}) == {"namespace": {"$in": ["wip"]}}
    # No clause: the query stands as it was.
    assert and_namespace({"namespace": "wip"}, None) == {"namespace": "wip"}


async def _seed(client: AsyncClient, headers: dict, namespace: str, national_id: str) -> str:
    resp = await client.post(f"{API}/documents", headers=headers, json=[{
        "namespace": namespace, "template_id": "wip:PERSON",
        "data": {"national_id": national_id, "first_name": namespace,
                "last_name": "Case1275Scope"},
    }])
    assert resp.status_code == 200, resp.text
    result = resp.json()["results"][0]
    assert result["status"] in ("created", "updated"), result
    return result["document_id"]


@pytest.mark.asyncio
async def test_a_query_that_filters_on_namespace_keeps_that_filter(client, auth_headers):
    wip = await _seed(client, auth_headers, "wip", "127500001")
    closed = await _seed(client, auth_headers, "xns-closed", "127500002")

    async def namespaces(body: dict) -> set[str]:
        resp = await client.post(f"{API}/documents/query", headers=BOTH_READER, json=body)
        assert resp.status_code == 200, resp.text
        return {i["namespace"] for i in resp.json()["items"]}

    both = await namespaces(
        {"filters": [{"field": "data.last_name", "operator": "eq", "value": "Case1275Scope"}]}
    )
    assert both == {"wip", "xns-closed"}, (wip, closed)

    narrowed = await namespaces({
        "filters": [
            {"field": "data.last_name", "operator": "eq", "value": "Case1275Scope"},
            {"field": "namespace", "operator": "eq", "value": "wip"},
        ],
    })
    assert narrowed == {"wip"}

    # A namespace the caller cannot read answers empty, not "everything you can
    # read instead": the two conditions hold together.
    elsewhere = await namespaces({
        "filters": [
            {"field": "data.last_name", "operator": "eq", "value": "Case1275Scope"},
            {"field": "namespace", "operator": "eq", "value": "another-ns"},
        ],
    })
    assert elsewhere == set()


@pytest.mark.asyncio
async def test_the_table_routes_refuse_an_unreadable_namespace_before_using_it(
    client, auth_headers
):
    """`wip:PERSON` resolves wherever it is asked, so the refusal here can only
    come from the namespace — unlike a bare `PERSON`, which fails the name
    lookup first and made the old assertion pass whatever the route did."""
    await _seed(client, auth_headers, "xns-closed", "127500003")

    listed = await client.get(
        f"{API}/documents", headers=WIP_READER, params={"namespace": "xns-closed"},
    )
    assert listed.status_code == 404, listed.text

    for path in ("table/wip:PERSON", "table/wip:PERSON/csv"):
        resp = await client.get(
            f"{API}/{path}", headers=WIP_READER, params={"namespace": "xns-closed"},
        )
        assert resp.status_code == listed.status_code, (path, resp.text)
        assert "127500003" not in resp.text

    # The same key on its own namespace still works: the refusal is the
    # namespace, not the route.
    ok = await client.get(
        f"{API}/table/wip:PERSON", headers=WIP_READER, params={"namespace": "wip"},
    )
    assert ok.status_code == 200, ok.text
