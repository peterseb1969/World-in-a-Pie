"""A document retirement or revival the Registry cannot record is not written.

A soft delete retires the document; a write to a deleted document's identity
revives it. Each asks the Registry first: when it does not answer, or
refuses, document-store writes nothing and the item is an error
(registry_error; rule registry_unavailable for an outage), alone and in a
batch — the batch's other items stand. When the document write fails after
the Registry recorded the change, the change is undone.
"""

import pytest
from httpx import AsyncClient

from document_store.models.document import Document, DocumentStatus
from document_store.services import document_service
from document_store.services.document_service import DocumentService
from wip_auth.registry_client import RegistryUnavailableError

PERSON = "PERSON"
URL = "/api/document-store/documents"


def _person(national_id: str, first_name: str = "John") -> dict:
    return {
        "national_id": national_id, "first_name": first_name, "last_name": "Doe",
        "birth_date": "1990-01-15", "gender": "M", "age": 34,
    }


async def _post(client: AsyncClient, auth_headers: dict, *national_ids: str) -> list[dict]:
    payload = [
        {"namespace": "wip", "template_id": PERSON, "data": _person(n)} for n in national_ids
    ]
    resp = await client.post(URL, headers=auth_headers, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


async def _delete(client: AsyncClient, auth_headers: dict, *document_ids: str) -> list[dict]:
    resp = await client.request(
        "DELETE", URL, headers=auth_headers, json=[{"id": d} for d in document_ids],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


async def _active(document_id: str) -> bool:
    return await Document.find_one(
        {"document_id": document_id, "status": DocumentStatus.ACTIVE.value},
    ) is not None


@pytest.fixture
def registry(monkeypatch):
    """The in-process Registry client, with spies on the lifecycle calls."""
    client = document_service.get_registry_client()
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
    return client


def _unreachable(monkeypatch, registry, verb, *, only: str | None = None):
    """The Registry does not answer for ``verb`` (for every entry, or only
    for the entry ``only``)."""
    passthrough = getattr(registry, f"{verb}_entries")

    async def down(entry_ids, updated_by=None, **kw):
        if only is not None and only not in entry_ids:
            return await passthrough(entry_ids, updated_by=updated_by, **kw)
        registry.calls[verb].append(list(entry_ids))
        raise RegistryUnavailableError("Registry unavailable (entries): HTTP 503")
    monkeypatch.setattr(registry, f"{verb}_entries", down)


def _refusing(monkeypatch, registry, verb):
    async def refuse(entry_ids, updated_by=None, **kw):
        registry.calls[verb].append(list(entry_ids))
        return [{"registry_id": e, "status": "error", "error": "simulated refusal"} for e in entry_ids]
    monkeypatch.setattr(registry, f"{verb}_entries", refuse)


# ── soft delete ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_delete_is_not_written_when_the_registry_does_not_answer(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch,
):
    first, second = (r["document_id"] for r in await _post(client, auth_headers, "710000001", "710000002"))
    _unreachable(monkeypatch, registry, "deactivate", only=second)
    results = await _delete(client, auth_headers, first, second)
    assert results[0]["status"] == "deleted", results
    assert results[1]["status"] == "error", results
    assert results[1]["error_code"] == "registry_error"
    assert results[1]["details"] == {"rule": "registry_unavailable"}
    assert not await _active(first)
    assert await _active(second)


@pytest.mark.asyncio
async def test_a_delete_is_not_written_when_the_registry_refuses(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch,
):
    [created] = await _post(client, auth_headers, "710000003")
    _refusing(monkeypatch, registry, "deactivate")
    [row] = await _delete(client, auth_headers, created["document_id"])
    assert row["status"] == "error" and row["error_code"] == "registry_error", row
    assert await _active(created["document_id"])


@pytest.mark.asyncio
async def test_a_failed_delete_write_undoes_the_registry_change(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch,
):
    [created] = await _post(client, auth_headers, "710000004")
    document_id = created["document_id"]

    async def failing_save(self, *args, **kwargs):
        raise RuntimeError("mongo write failed")

    monkeypatch.setattr(Document, "save", failing_save)
    with pytest.raises(RuntimeError, match="mongo write failed"):
        await DocumentService().delete_document(document_id)
    assert registry.calls == {"deactivate": [[document_id]], "reactivate": [[document_id]]}


# ── revival ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
async def test_a_revival_is_not_written_when_the_registry_does_not_answer(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch, bulk: bool,
):
    national_id = "720000001" if not bulk else "720000002"
    [created] = await _post(client, auth_headers, national_id)
    document_id = created["document_id"]
    await _delete(client, auth_headers, document_id)

    _unreachable(monkeypatch, registry, "reactivate")
    others = ("720000099",) if bulk else ()
    results = await _post(client, auth_headers, national_id, *others)
    assert results[0]["status"] == "error", results
    assert results[0]["error_code"] == "registry_error"
    assert results[0]["details"] == {"rule": "registry_unavailable"}
    assert not await _active(document_id)
    assert await Document.find({"document_id": document_id}).count() == 1  # no new version
    if bulk:
        assert results[1]["status"] == "created", results


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
async def test_a_revival_is_not_written_when_the_registry_refuses(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch, bulk: bool,
):
    national_id = "720000003" if not bulk else "720000004"
    [created] = await _post(client, auth_headers, national_id)
    document_id = created["document_id"]
    await _delete(client, auth_headers, document_id)

    _refusing(monkeypatch, registry, "reactivate")
    others = ("720000098",) if bulk else ()
    results = await _post(client, auth_headers, national_id, *others)
    assert results[0]["status"] == "error" and results[0]["error_code"] == "registry_error", results
    assert not await _active(document_id)


@pytest.mark.asyncio
async def test_an_overwrite_in_place_revival_is_not_written_when_the_registry_does_not_answer(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch,
):
    item = {"namespace": "wip", "template_id": "LATEST_ONLY_NOTE",
            "data": {"note_id": "N1196", "body": "first"}}
    resp = await client.post(URL, headers=auth_headers, json=[item])
    document_id = resp.json()["results"][0]["document_id"]
    await _delete(client, auth_headers, document_id)

    _unreachable(monkeypatch, registry, "reactivate")
    item["data"]["body"] = "second"
    resp = await client.post(URL, headers=auth_headers, json=[item])
    [row] = resp.json()["results"]
    assert row["status"] == "error" and row["error_code"] == "registry_error", row
    stored = await Document.find_one({"document_id": document_id})
    assert stored.status == DocumentStatus.INACTIVE
    assert stored.data["body"] == "first"


@pytest.mark.asyncio
async def test_a_failed_revival_write_undoes_the_registry_change(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch,
):
    [created] = await _post(client, auth_headers, "720000005")
    document_id = created["document_id"]
    await _delete(client, auth_headers, document_id)
    registry.calls["deactivate"].clear()

    async def failing_insert(self, *args, **kwargs):
        raise RuntimeError("mongo write failed")

    monkeypatch.setattr(DocumentService, "_insert_or_restore", failing_insert)
    with pytest.raises(RuntimeError, match="mongo write failed"):
        await _post(client, auth_headers, "720000005")
    assert registry.calls == {"reactivate": [[document_id]], "deactivate": [[document_id]]}
    assert not await _active(document_id)
