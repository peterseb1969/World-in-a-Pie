"""Hard-deleting from a mutable terminology removes the Registry entries too.

Terms of a mutable terminology are hard-deletable regardless of the
namespace's deletion_mode (the test namespace is in the default 'retain'
mode). Before, the Registry refused to delete their entries — as a per-item
error inside HTTP 200 that nobody read — so a deleted term's value kept
resolving through the Registry, and re-creating the value reused the dead
id. When the entry really cannot be removed, the caller is told.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from def_store.services.registry_client import RegistryError
from registry.main import app as registry_app


async def _registry_entry_status(entry_id: str) -> int:
    headers = {"X-API-Key": os.environ["MASTER_API_KEY"]}
    async with AsyncClient(transport=ASGITransport(app=registry_app), base_url="http://registry") as reg:
        return (await reg.get(f"/api/registry/entries/{entry_id}", headers=headers)).status_code


async def _mutable(client, headers, value="CLEANUP_PEOPLE"):
    r = await client.post("/api/def-store/terminologies", headers=headers, json=[{
        "value": value, "label": value, "namespace": "wip", "mutable": True,
    }])
    tid = r.json()["results"][0]["id"]
    r = await client.post(f"/api/def-store/terminologies/{tid}/terms", headers=headers, json=[
        {"value": "ADA"}, {"value": "LOVELACE"},
    ])
    return tid, {x["value"]: x["id"] for x in r.json()["results"]}


@pytest.mark.asyncio
async def test_term_hard_delete_removes_registry_entry_and_frees_the_value(client, auth_headers):
    tid, ids = await _mutable(client, auth_headers)
    assert await _registry_entry_status(ids["LOVELACE"]) == 200

    r = await client.request("DELETE", "/api/def-store/terms", headers=auth_headers, json=[{"id": ids["LOVELACE"]}])
    assert r.json()["results"][0]["status"] == "deleted", r.json()
    assert await _registry_entry_status(ids["LOVELACE"]) == 404

    # the value is free again: re-creating it is a new term, not the dead id
    r = await client.post(f"/api/def-store/terminologies/{tid}/terms", headers=auth_headers, json=[{"value": "LOVELACE"}])
    new_id = r.json()["results"][0]["id"]
    assert new_id and new_id != ids["LOVELACE"]


@pytest.mark.asyncio
async def test_terminology_hard_delete_removes_all_registry_entries(client, auth_headers):
    tid, ids = await _mutable(client, auth_headers, "CLEANUP_ALL")
    r = await client.request("DELETE", "/api/def-store/terminologies", headers=auth_headers, json=[{"id": tid}])
    assert r.json()["results"][0]["status"] == "deleted", r.json()
    for entry_id in (tid, *ids.values()):
        assert await _registry_entry_status(entry_id) == 404, entry_id


@pytest.mark.asyncio
async def test_registry_refusal_is_reported_not_swallowed(client, auth_headers):
    """If the Registry still refuses, the term is gone but the caller hears
    that its entry is not — with a machine-readable code."""
    _tid, ids = await _mutable(client, auth_headers, "CLEANUP_REFUSED")
    from def_store.services import terminology_service

    registry = terminology_service.get_registry_client()
    with patch.object(registry, "hard_delete_entry", AsyncMock(side_effect=RegistryError("refused"))):
        r = await client.request("DELETE", "/api/def-store/terms", headers=auth_headers, json=[{"id": ids["ADA"]}])
    item = r.json()["results"][0]
    assert item["status"] == "error"
    assert item["error_code"] == "registry_entry_not_removed"
    assert "was deleted" in item["error"]
    assert (await client.get(f"/api/def-store/terms/{ids['ADA']}", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_registry_ghost_cleanup_removes_only_safe_ghosts(client, auth_headers):
    """Ghosts as the old code left them: the def-store term gone, its
    Registry entry active. Dry run reports; apply removes the mutable one and
    leaves the one from an immutable terminology for a human."""
    from def_store.models.term import Term

    _tid, ids = await _mutable(client, auth_headers, "GHOST_MUTABLE")
    r = await client.post("/api/def-store/terminologies", headers=auth_headers, json=[{
        "value": "GHOST_FIXED", "label": "Fixed", "namespace": "wip",
    }])
    fixed = r.json()["results"][0]["id"]
    r = await client.post(f"/api/def-store/terminologies/{fixed}/terms", headers=auth_headers, json=[{"value": "KEEP"}])
    fixed_term = r.json()["results"][0]["id"]
    for term_id in (ids["LOVELACE"], fixed_term):
        await Term.find_one({"term_id": term_id}).delete()

    url = "/api/def-store/maintenance/registry-ghosts"
    report = (await client.post(url, headers=auth_headers, params={"namespace": "wip"})).json()
    assert report["dry_run"] is True and report["removed"] == []
    by_id = {g["entry_id"]: g for g in report["ghosts"]}
    assert by_id[ids["LOVELACE"]]["removable"] is True
    assert by_id[fixed_term]["removable"] is False
    assert ids["ADA"] not in by_id

    applied = (await client.post(url, headers=auth_headers, params={"namespace": "wip", "dry_run": "false"})).json()
    assert ids["LOVELACE"] in applied["removed"]
    assert fixed_term not in applied["removed"]
    assert applied["errors"] == []
    assert await _registry_entry_status(ids["LOVELACE"]) == 404
    assert await _registry_entry_status(fixed_term) == 200
