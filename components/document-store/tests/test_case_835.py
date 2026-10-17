"""Write doors resolve against the Registry, not the cache (CASE-835).

After an entity is hard-deleted and re-created under the same value, the
process-local resolution cache (wip-auth, 300 s TTL) keeps serving the DEAD
canonical id while the Registry answers correctly the whole time. A write
door that trusts the cache then fails on an id that names nothing
("Template '<old-uuid>' not found" — the filed reproduction) or acts on a
stale id. Write doors therefore resolve with bypass_cache=True; read doors
keep the cache (their staleness is bounded by the TTL and self-heals).

Two layers of coverage:

- an end-to-end replay of the reproduction against the create door: warm
  the cache for PERSON, re-point the registry entry to a new id (the
  delete + re-create), teach the mock Template Store the new incarnation
  only, then create a document by value — it must land on the NEW id;
- spy tests pinning the bypass flag on every other write door (patch,
  migrate, delete, archive, file metadata, file delete, CSV import),
  so dropping the flag on any one of them fails a test by name.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from registry.models.entry import RegistryEntry
from wip_auth.resolve import resolve_entity_id

from .conftest import SAMPLE_TEMPLATES

NEW_ID = "019fffff-0000-7000-8000-000000000835"


def _person_template_id() -> str:
    """The current PERSON canonical id (SAMPLE_TEMPLATES is keyed by both
    the canonical UUID7 and the legacy key; the value field disambiguates)."""
    return next(
        t["template_id"] for t in SAMPLE_TEMPLATES.values() if t["value"] == "PERSON"
    )


async def _delete_and_recreate_person_template(old_id: str) -> None:
    """Simulate hard-delete + re-create of the PERSON template.

    The Registry's entry is re-pointed to a new canonical id (resolution by
    value answers the new id live from here on), and the mock Template Store
    forgets the old incarnation and learns the new one — the state the filed
    reproduction had after step 2.
    """
    entry = await RegistryEntry.find_one({"entry_id": old_id})
    assert entry is not None, "PERSON registry entry missing"
    entry.entry_id = NEW_ID
    await entry.save()

    SAMPLE_TEMPLATES[NEW_ID] = {**SAMPLE_TEMPLATES[old_id], "template_id": NEW_ID}
    del SAMPLE_TEMPLATES[old_id]


@pytest.mark.asyncio
async def test_create_by_value_survives_delete_and_recreate(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """The filed reproduction, in-process: a warm cache holds the dead id,
    and the create door must still land on the Registry's current one."""
    old_id = _person_template_id()

    # Warm the resolution cache with the OLD incarnation (any read does this).
    assert await resolve_entity_id("PERSON", "template", "wip") == old_id

    await _delete_and_recreate_person_template(old_id)

    # Control (the case's step 4): the Registry answers the NEW id.
    assert (
        await resolve_entity_id("PERSON", "template", "wip", bypass_cache=True)
        == NEW_ID
    )

    # The write door must see it too — without bypass this resolved the
    # cached dead id and failed with "Template '<old-uuid>' not found".
    response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[{"namespace": "wip", "template_id": "PERSON", "data": sample_person_data}],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["succeeded"] == 1, f"create failed: {bulk}"
    document_id = bulk["results"][0]["document_id"]

    get_resp = await client.get(
        f"/api/document-store/documents/{document_id}", headers=auth_headers
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["template_id"] == NEW_ID


# ---------------------------------------------------------------------------
# Door-contract pins: every write door passes bypass_cache=True.
# The resolver is spied per api module; the request only has to get past
# body validation far enough to reach the resolve call, so each spy test
# asserts the call's kwargs and nothing about the (failing) outcome.
# ---------------------------------------------------------------------------


def _bypass_kwarg(spy: AsyncMock) -> bool:
    assert spy.await_count >= 1, "door never reached its resolve call"
    return bool(spy.await_args.kwargs.get("bypass_cache"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PATCH", "/api/document-store/documents",
         [{"document_id": "019fffff-0000-7000-8000-00000000dead", "patch": {"x": 1}}]),
        ("DELETE", "/api/document-store/documents",
         [{"id": "019fffff-0000-7000-8000-00000000dead"}]),
        ("POST", "/api/document-store/documents/archive",
         [{"id": "019fffff-0000-7000-8000-00000000dead"}]),
    ],
    ids=["patch", "delete", "archive"],
)
async def test_document_write_doors_bypass_cache(
    client: AsyncClient, auth_headers: dict, method: str, path: str, body: list
):
    spy = AsyncMock(return_value=None)
    with patch("document_store.api.documents.resolve_bulk_ids", spy):
        await client.request(method, path, headers=auth_headers, json=body)
    assert _bypass_kwarg(spy), f"{method} {path} resolves without bypass_cache"


@pytest.mark.asyncio
async def test_migrate_door_bypasses_cache(client: AsyncClient, auth_headers: dict):
    spy = AsyncMock(return_value="019fffff-0000-7000-8000-00000000dead")
    with patch("document_store.api.documents.resolve_or_404", spy):
        await client.post(
            "/api/document-store/documents/migrate",
            headers=auth_headers,
            params={"namespace": "wip"},
            json={"template_id": "PERSON", "from_version": 1, "to_version": 2},
        )
    assert _bypass_kwarg(spy), "migrate resolves without bypass_cache"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "body"),
    [
        ("PATCH", [{"file_id": "019fffff-0000-7000-8000-00000000dead"}]),
        ("DELETE", [{"id": "019fffff-0000-7000-8000-00000000dead"}]),
    ],
    ids=["file-metadata", "file-delete"],
)
async def test_file_write_doors_bypass_cache(
    client: AsyncClient, auth_headers: dict, method: str, body: list
):
    spy = AsyncMock(return_value=None)
    with (
        # The delete door is gated on file storage being enabled; the gate
        # fires before the handler, so open it for the spy to be reachable.
        patch("document_store.api.files.is_file_storage_enabled", return_value=True),
        patch("document_store.api.files.resolve_bulk_ids", spy),
    ):
        await client.request(
            method, "/api/document-store/files", headers=auth_headers, json=body
        )
    assert _bypass_kwarg(spy), f"{method} /files resolves without bypass_cache"


@pytest.mark.asyncio
async def test_import_door_bypasses_cache(client: AsyncClient, auth_headers: dict):
    spy = AsyncMock(return_value="019fffff-0000-7000-8000-00000000dead")
    with patch("document_store.api.import_api.resolve_or_404", spy):
        await client.post(
            "/api/document-store/import",
            headers=auth_headers,
            files={"file": ("probe.csv", b"a,b\n1,2\n", "text/csv")},
            data={
                "template_id": "PERSON",
                "column_mapping": json.dumps({"a": "first_name"}),
                "namespace": "wip",
            },
        )
    assert _bypass_kwarg(spy), "CSV import resolves without bypass_cache"
