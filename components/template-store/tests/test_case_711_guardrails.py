"""Version-change guardrails on the template side.

- Declared renames: a version's {new_field: old_field} declaration is
  validated against the version it renames from (old existed and is gone,
  new is declared and is new, types match, identity fields excluded) and
  rejected on a first version (nothing to rename from).
- Impact analysis: a version event's result carries advisory live-document
  counts and a migration-eligibility verdict from document-store; an
  unreachable document-store yields an EXPLICIT unavailable marker, never a
  silent zero, and never blocks the version event.
"""

import pytest
from httpx import AsyncClient

from template_store.services.document_store_client import (
    DocumentStoreClient,
    set_document_store_client,
)

TEMPLATES = "/api/template-store/templates"


class _StubDocStore(DocumentStoreClient):
    """Canned impact stats, or a hard failure."""

    def __init__(self, stats: dict | None = None, fail: bool = False):
        super().__init__(base_url="http://stub", api_key="stub")
        self._stats = stats or {}
        self._fail = fail
        self.calls: list[dict] = []

    async def get_impact_stats(self, template_id, namespace, fields=None):
        self.calls.append({
            "template_id": template_id, "namespace": namespace, "fields": fields,
        })
        if self._fail:
            raise RuntimeError("document-store unreachable")
        return self._stats


@pytest.fixture(autouse=True)
def _reset_doc_store_client():
    yield
    set_document_store_client(None)


def _payload(value: str, fields: list[dict] | None = None, **overrides) -> dict:
    base = {
        "namespace": "wip",
        "value": value,
        "label": f"{value} label",
        "identity_fields": ["code"],
        "fields": fields or [
            {"name": "code", "label": "Code", "type": "string", "mandatory": True},
            {"name": "note", "label": "Note", "type": "string", "mandatory": False},
        ],
    }
    base.update(overrides)
    return base


async def _post(client: AsyncClient, auth_headers: dict, items: list[dict]) -> dict:
    resp = await client.post(TEMPLATES, headers=auth_headers, json=items)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# declared renames
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_declared_rename_versions_and_is_stored(client: AsyncClient, auth_headers: dict):
    set_document_store_client(_StubDocStore(stats={
        "total_live_docs": 0, "docs_per_version": {}, "field_nonempty_counts": {},
    }))
    await _post(client, auth_headers, [_payload("REN_OK")])

    v2 = _payload("REN_OK", fields=[
        {"name": "code", "label": "Code", "type": "string", "mandatory": True},
        {"name": "remark", "label": "Remark", "type": "string", "mandatory": False},
    ])
    v2["renames"] = {"remark": "note"}
    result = await _post(client, auth_headers, [v2])
    item = result["results"][0]
    assert item["status"] == "updated", item
    assert item["version"] == 2

    got = await client.get(
        f"{TEMPLATES}/by-value/REN_OK/versions/2?namespace=wip", headers=auth_headers
    )
    assert got.status_code == 200
    assert got.json()["renames"] == {"remark": "note"}


@pytest.mark.asyncio
async def test_rename_validation_rejects_bad_declarations(
    client: AsyncClient, auth_headers: dict
):
    await _post(client, auth_headers, [_payload("REN_BAD")])

    # Old field never existed in the previous version.
    v2 = _payload("REN_BAD", fields=[
        {"name": "code", "label": "Code", "type": "string", "mandatory": True},
        {"name": "remark", "label": "Remark", "type": "string", "mandatory": False},
    ])
    v2["renames"] = {"remark": "ghost"}
    res = await _post(client, auth_headers, [v2])
    assert res["results"][0]["status"] == "error"
    assert "does not exist in the previous version" in res["results"][0]["error"]

    # Identity fields cannot be renamed.
    v2b = _payload("REN_BAD", fields=[
        {"name": "id_code", "label": "Code", "type": "string", "mandatory": True},
        {"name": "note", "label": "Note", "type": "string", "mandatory": False},
    ], identity_fields=["code"])
    v2b["renames"] = {"id_code": "code"}
    res = await _post(client, auth_headers, [v2b])
    assert res["results"][0]["status"] == "error"
    assert "identity fields cannot be renamed" in res["results"][0]["error"]


@pytest.mark.asyncio
async def test_renames_rejected_on_first_version(client: AsyncClient, auth_headers: dict):
    first = _payload("REN_V1")
    first["renames"] = {"note": "old_note"}
    res = await _post(client, auth_headers, [first])
    assert res["results"][0]["status"] == "error"
    assert "previous version" in res["results"][0]["error"]


# ---------------------------------------------------------------------------
# impact analysis on version events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_version_event_carries_impact_and_eligibility(
    client: AsyncClient, auth_headers: dict
):
    """Dropping an empty field with live docs on record: counts appear in
    details.impact, and migration is offered as eligible."""
    stub = _StubDocStore(stats={
        "total_live_docs": 7,
        "docs_per_version": {"1": 7},
        "field_nonempty_counts": {"note": 0},
    })
    set_document_store_client(stub)
    await _post(client, auth_headers, [_payload("IMPACT_OK")])

    v2 = _payload("IMPACT_OK", fields=[
        {"name": "code", "label": "Code", "type": "string", "mandatory": True},
    ])
    result = await _post(client, auth_headers, [v2])
    item = result["results"][0]
    assert item["status"] == "updated"
    details = item["details"]
    assert details["impact"]["status"] == "ok"
    assert details["impact"]["total_live_docs"] == 7
    assert details["migration"]["eligible"] is True
    assert "empty in all live documents" in details["migration"]["reason"]
    # The dropped field was what we asked document-store to count.
    assert stub.calls and stub.calls[-1]["fields"] == ["note"]


@pytest.mark.asyncio
async def test_version_event_flags_stranded_data(client: AsyncClient, auth_headers: dict):
    stub = _StubDocStore(stats={
        "total_live_docs": 7,
        "docs_per_version": {"1": 7},
        "field_nonempty_counts": {"note": 4},
    })
    set_document_store_client(stub)
    await _post(client, auth_headers, [_payload("IMPACT_STRAND")])

    v2 = _payload("IMPACT_STRAND", fields=[
        {"name": "code", "label": "Code", "type": "string", "mandatory": True},
    ])
    item = (await _post(client, auth_headers, [v2]))["results"][0]
    assert item["status"] == "updated"
    assert item["details"]["migration"]["eligible"] is False
    assert "carry live data" in item["details"]["migration"]["reason"]


@pytest.mark.asyncio
async def test_unreachable_doc_store_is_explicit_never_blocking(
    client: AsyncClient, auth_headers: dict
):
    """The advisory call failing must not block the version event, and must
    surface as an explicit unavailable marker — not a silent zero."""
    set_document_store_client(_StubDocStore(fail=True))
    await _post(client, auth_headers, [_payload("IMPACT_DOWN")])

    v2 = _payload("IMPACT_DOWN", fields=[
        {"name": "code", "label": "Code", "type": "string", "mandatory": True},
    ])
    item = (await _post(client, auth_headers, [v2]))["results"][0]
    assert item["status"] == "updated"
    assert item["version"] == 2
    assert item["details"]["impact"]["status"] == "unavailable"
    assert item["details"]["migration"]["eligible"] is None


@pytest.mark.asyncio
async def test_declared_rename_neutralizes_stranded_check(
    client: AsyncClient, auth_headers: dict
):
    """A removed field that is a declared rename source is not stranded data
    — the rename migrates it losslessly, so eligibility ignores its count."""
    stub = _StubDocStore(stats={
        "total_live_docs": 3,
        "docs_per_version": {"1": 3},
        "field_nonempty_counts": {},
    })
    set_document_store_client(stub)
    await _post(client, auth_headers, [_payload("REN_IMPACT")])

    v2 = _payload("REN_IMPACT", fields=[
        {"name": "code", "label": "Code", "type": "string", "mandatory": True},
        {"name": "remark", "label": "Remark", "type": "string", "mandatory": False},
    ])
    v2["renames"] = {"remark": "note"}
    item = (await _post(client, auth_headers, [v2]))["results"][0]
    assert item["status"] == "updated"
    assert item["details"]["migration"]["eligible"] is True
    assert "additive or rename-only" in item["details"]["migration"]["reason"]
    # note is rename-excluded: nothing was asked of document-store for it.
    assert stub.calls[-1]["fields"] == []
