"""A retirement or revival the Registry cannot record is not written.

Deprecating or soft-deleting a term, soft-deleting a terminology and
restoring one each ask the Registry first. When it does not answer, or
refuses an entry, def-store writes nothing (registry_error; restore is a
503) and what the Registry did record is undone. When the def-store write
fails after the Registry recorded the change, the Registry change is undone.
"""

import uuid

import pytest
from httpx import AsyncClient

from def_store.models.term import Term
from def_store.models.terminology import Terminology
from def_store.services import terminology_service
from def_store.services.registry_client import RegistryError
from def_store.services.terminology_service import TerminologyService

API = "/api/def-store"


async def _terminology(client, auth_headers) -> str:
    value = f"LIFE_{uuid.uuid4().hex[:8].upper()}"
    resp = await client.post(f"{API}/terminologies", headers=auth_headers,
                             json=[{"value": value, "label": value, "namespace": "wip"}])
    return resp.json()["results"][0]["id"]


async def _terms(client, auth_headers, tid, *values) -> list[str]:
    resp = await client.post(f"{API}/terminologies/{tid}/terms", headers=auth_headers,
                             json=[{"value": v} for v in values])
    return [r["id"] for r in resp.json()["results"]]


@pytest.fixture
def registry(monkeypatch):
    """The in-process Registry client, with spies on the lifecycle calls."""
    client = terminology_service.get_registry_client()
    calls: dict[str, list[list[str]]] = {"deactivate": [], "reactivate": []}
    real = {"deactivate": client.deactivate_entries, "reactivate": client.reactivate_entries}

    def spy(verb):
        async def call(entry_ids, updated_by=None, **kw):
            calls[verb].append(list(entry_ids))
            return await real[verb](entry_ids, updated_by=updated_by, **kw)
        return call

    monkeypatch.setattr(client, "deactivate_entries", spy("deactivate"))
    monkeypatch.setattr(client, "reactivate_entries", spy("reactivate"))
    client.calls = calls
    client.real = real
    return client


def _unreachable(monkeypatch, registry, verb):
    async def down(entry_ids, updated_by=None, **kw):
        raise RegistryError("registry unreachable")
    monkeypatch.setattr(registry, f"{verb}_entries", down)


@pytest.mark.asyncio
async def test_deprecate_is_not_written_when_the_registry_does_not_answer(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch
):
    tid = await _terminology(client, auth_headers)
    (old,) = await _terms(client, auth_headers, tid, "old")
    _unreachable(monkeypatch, registry, "deactivate")
    resp = await client.post(f"{API}/terms/deprecate", headers=auth_headers,
                             json=[{"term_id": old, "reason": "test"}])
    row = resp.json()["results"][0]
    assert row["status"] == "error" and row["error_code"] == "registry_error", row
    assert (await Term.find_one({"term_id": old})).status == "active"


@pytest.mark.asyncio
async def test_term_soft_delete_is_not_written_when_the_registry_does_not_answer(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch
):
    tid = await _terminology(client, auth_headers)
    (term,) = await _terms(client, auth_headers, tid, "term")
    _unreachable(monkeypatch, registry, "deactivate")
    resp = await client.request("DELETE", f"{API}/terms", headers=auth_headers, json=[{"id": term}])
    row = resp.json()["results"][0]
    assert row["status"] == "error" and row["error_code"] == "registry_error", row
    assert (await Term.find_one({"term_id": term})).status == "active"


@pytest.mark.asyncio
async def test_a_refused_entry_undoes_what_the_registry_recorded(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch
):
    tid = await _terminology(client, auth_headers)
    a, b = await _terms(client, auth_headers, tid, "a", "b")

    async def one_refused(entry_ids, updated_by=None, **kw):
        registry.calls["deactivate"].append(list(entry_ids))
        results = await registry.real["deactivate"](entry_ids, updated_by=updated_by, **kw)
        for r in results:
            if r.get("registry_id") == b:
                r["status"], r["error"] = "error", "simulated refusal"
        return results

    monkeypatch.setattr(registry, "deactivate_entries", one_refused)
    resp = await client.request("DELETE", f"{API}/terminologies", headers=auth_headers, json=[{"id": tid}])
    row = resp.json()["results"][0]
    assert row["status"] == "error" and row["error_code"] == "registry_error", row

    assert (await Terminology.find_one({"terminology_id": tid})).status == "active"
    assert {t.status for t in await Term.find({"terminology_id": tid}).to_list()} == {"active"}
    # What the Registry recorded (the terminology and `a`) is revived again
    (undone,) = registry.calls["reactivate"]
    assert set(undone) == {tid, a}


@pytest.mark.asyncio
async def test_restore_is_a_503_and_changes_nothing_when_the_registry_does_not_answer(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch
):
    tid = await _terminology(client, auth_headers)
    await _terms(client, auth_headers, tid, "a", "b")
    resp = await client.request("DELETE", f"{API}/terminologies", headers=auth_headers, json=[{"id": tid}])
    assert resp.json()["succeeded"] == 1, resp.json()

    _unreachable(monkeypatch, registry, "reactivate")
    resp = await client.post(f"{API}/terminologies/{tid}/restore", headers=auth_headers)
    assert resp.status_code == 503, resp.text
    assert (await Terminology.find_one({"terminology_id": tid})).status == "inactive"
    assert {t.status for t in await Term.find({"terminology_id": tid}).to_list()} == {"inactive"}


@pytest.mark.asyncio
async def test_a_failed_def_store_write_undoes_the_registry_change(
    client: AsyncClient, auth_headers: dict, registry
):
    async def failing_write():
        raise RuntimeError("mongo write failed")

    tid = await _terminology(client, auth_headers)
    (entry,) = await _terms(client, auth_headers, tid, "x")
    with pytest.raises(RuntimeError):
        await TerminologyService._lifecycle_write([entry], retired=True, actor="test", write=failing_write)
    assert registry.calls["deactivate"] == [[entry]]
    assert registry.calls["reactivate"] == [[entry]]
