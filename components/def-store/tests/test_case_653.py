"""CSV imports are permission-gated and namespace-addressed.

The write-permission gate on POST /import-export/import only ran when it
could read data["terminology"]["namespace"] — the CSV payload is flat by
design, so every CSV import skipped the gate. The same missing namespace
also made CSV creation of a NEW terminology impossible (the rebuilt JSON
block had no namespace and the create path requires one). Fixed by a
`namespace` query parameter (mirroring /export), single-namespace-key
derivation as fallback, a loud 400 when unresolvable, and a
namespace-scoped existing-terminology lookup.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import AsyncClient

CSV_PAYLOAD = {
    "terminology_value": "CASE653_CSV",
    "terminology_label": "CSV Imported",
    "csv_content": "value,label,description,sort_order\nred,Red,,1\nblue,Blue,,2\n",
}


@pytest.mark.asyncio
async def test_csv_import_calls_permission_gate(
    client: AsyncClient, auth_headers: dict
):
    """THE regression: the write gate must fire on the CSV path. Patch the
    check to deny unconditionally — if the endpoint wires it, we get the
    denial; under the old skip-when-no-nested-namespace logic the import
    would sail through."""
    from fastapi import HTTPException

    async def deny(identity, namespace, level):
        raise HTTPException(404, "Namespace not found")

    with patch(
        "def_store.api.import_export.check_namespace_permission",
        side_effect=deny,
    ):
        resp = await client.post(
            "/api/def-store/import-export/import?format=csv&namespace=wip",
            headers=auth_headers,
            json=CSV_PAYLOAD,
        )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_csv_import_creates_new_terminology_with_namespace_param(
    client: AsyncClient, auth_headers: dict
):
    """The previously-broken path: CSV import of a NEW terminology. Before
    the fix the rebuilt JSON block carried no namespace and the create path
    KeyErrored; now the query param threads through."""
    resp = await client.post(
        "/api/def-store/import-export/import?format=csv&namespace=wip",
        headers=auth_headers,
        json=CSV_PAYLOAD,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["terminology"]["status"] == "created"
    assert body["terms_result"]["succeeded"] == 2

    verify = await client.get(
        "/api/def-store/terminologies/by-value/CASE653_CSV",
        headers=auth_headers,
    )
    assert verify.status_code == 200
    assert verify.json()["namespace"] == "wip"


@pytest.mark.asyncio
async def test_import_without_resolvable_namespace_is_a_loud_400(
    client: AsyncClient, auth_headers: dict
):
    """No param, flat CSV payload, and an unscoped (admin) key that can't
    derive a single namespace → explicit 400, never a silent skip."""
    resp = await client.post(
        "/api/def-store/import-export/import?format=csv",
        headers=auth_headers,
        json={**CSV_PAYLOAD, "terminology_value": "CASE653_NO_NS"},
    )
    assert resp.status_code == 400, resp.text
    assert "namespace is required" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_json_body_and_param_conflict_is_a_loud_400(
    client: AsyncClient, auth_headers: dict
):
    """Two explicit namespace sources that disagree must 400 — silently
    preferring either would let one smuggle the write past the other."""
    resp = await client.post(
        "/api/def-store/import-export/import?format=json&namespace=other-ns",
        headers=auth_headers,
        json={
            "terminology": {
                "value": "CASE653_CONFLICT",
                "label": "Conflict",
                "namespace": "wip",
            },
            "terms": [],
        },
    )
    assert resp.status_code == 400, resp.text
    assert "contradicts" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_json_import_with_matching_param_still_works(
    client: AsyncClient, auth_headers: dict
):
    """Body namespace + agreeing param is fine; the JSON path is unchanged
    apart from the gate now being unconditional."""
    resp = await client.post(
        "/api/def-store/import-export/import?format=json&namespace=wip",
        headers=auth_headers,
        json={
            "terminology": {
                "value": "CASE653_JSON",
                "label": "JSON Imported",
                "namespace": "wip",
            },
            "terms": [{"value": "one", "label": "One"}],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["terminology"]["status"] == "created"


@pytest.mark.asyncio
async def test_existing_terminology_lookup_is_namespace_scoped(
    client: AsyncClient, auth_headers: dict
):
    """The dedup lookup must be (namespace, value), not value-only — an
    unscoped match could attach imported terms to a same-named terminology
    in ANOTHER namespace, sidestepping the permission gate. Captures the
    Mongo filter the service issues."""
    from def_store.models.terminology import Terminology

    captured: list[dict] = []
    original = Terminology.find_one

    def spy(filter_dict, *args, **kwargs):
        if isinstance(filter_dict, dict) and "value" in filter_dict:
            captured.append(filter_dict)
        return original(filter_dict, *args, **kwargs)

    with patch.object(Terminology, "find_one", side_effect=spy):
        resp = await client.post(
            "/api/def-store/import-export/import?format=csv&namespace=wip",
            headers=auth_headers,
            json={**CSV_PAYLOAD, "terminology_value": "CASE653_SCOPED"},
        )
    assert resp.status_code == 200, resp.text
    import_lookups = [f for f in captured if f.get("value") == "CASE653_SCOPED"]
    assert import_lookups, "import never looked up the terminology by value"
    for f in import_lookups:
        assert f.get("namespace") == "wip", f"unscoped lookup filter: {f}"
