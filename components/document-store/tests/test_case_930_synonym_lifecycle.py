"""CASE-930 — a document's retirement and revival reach the Registry.

Soft-deleting a document releases its identity-values synonym (and any
caller-added synonym): the synonym stays recorded on the Registry entry as
inactive, so it neither blocks a different identity with the same spellings
nor resolves. The canonical ID and the {ns, type, template, identity_hash}
auto-synonym stay, so re-posting the identity still revives the document —
and the revival claims the released synonym again, unless another entity
took it meanwhile, in which case the result says so.

A delete or revival the Registry cannot record is not written at all (see
test_registry_lifecycle_fails_write.py). /maintenance/reconcile-synonym-
lifecycle settles documents whose Registry state drifted anyway — retired
before deletes told the Registry anything, or a failed undo.
"""

import os

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from document_store.models.document import Document
from document_store.services.backup_engine import DirectRestoreEngine
from registry.main import app as registry_app
from registry.models.entry import RegistryEntry

PERSON = "PERSON"


def _person(national_id: str, first_name: str = "John") -> dict:
    return {
        "national_id": national_id,
        "first_name": first_name,
        "last_name": "Doe",
        "birth_date": "1990-01-15",
        "gender": "M",
        "age": 34,
    }


def _spelling(national_id: str) -> dict:
    return {"@template": PERSON, "national_id": national_id}


async def _post(client: AsyncClient, auth_headers: dict, items: list[dict]) -> list[dict]:
    payload = [{"namespace": "wip", **item} for item in items]
    response = await client.post("/api/document-store/documents", headers=auth_headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()["results"]


async def _delete(client: AsyncClient, auth_headers: dict, document_id: str) -> dict:
    response = await client.request(
        "DELETE", "/api/document-store/documents",
        headers=auth_headers, json=[{"id": document_id}],
    )
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["status"] == "deleted"
    return result


async def _synonym_states(document_id: str) -> dict[str, str]:
    """{"spelling" | "auto": status} for the document's Registry synonyms."""
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == document_id)
    assert entry is not None
    return {
        ("spelling" if "@template" in s.composite_key else "auto"): s.status
        for s in entry.synonyms
    }


async def _revive(client, auth_headers, national_id: str, *, bulk: bool) -> dict:
    items = [{"template_id": PERSON, "data": _person(national_id)}]
    if bulk:
        items.append({"template_id": PERSON, "data": _person("999999999", "Other")})
    return (await _post(client, auth_headers, items))[0]


async def _claim_elsewhere(key: dict) -> str:
    """Another Registry entry takes the key — the path B shape: a different
    identity registered with the same spellings."""
    async with AsyncClient(
        transport=ASGITransport(app=registry_app), base_url="http://registry",
    ) as registry:
        r = await registry.post(
            "/api/registry/entries/register",
            headers={"X-API-Key": os.environ["MASTER_API_KEY"]},
            json=[{
                "namespace": "wip", "entity_type": "documents",
                "composite_key": {"ns": "wip", "template_id": "elsewhere"},
                "identity_values": {"national_id": "another-referent"},
                "synonym_values": key,
            }],
        )
    result = r.json()["results"][0]
    assert result["status"] == "created", result
    return result["registry_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
async def test_delete_releases_and_revival_reclaims(client: AsyncClient, auth_headers: dict, bulk: bool):
    national_id = "111111111" if not bulk else "222222222"
    [created] = await _post(client, auth_headers, [{"template_id": PERSON, "data": _person(national_id)}])
    document_id = created["document_id"]
    assert await _synonym_states(document_id) == {"spelling": "active", "auto": "active"}

    deleted = await _delete(client, auth_headers, document_id)
    assert deleted.get("warnings") in (None, [])
    assert await _synonym_states(document_id) == {"spelling": "inactive", "auto": "active"}

    revived = await _revive(client, auth_headers, national_id, bulk=bulk)
    assert revived["status"] == "updated"
    assert revived["document_id"] == document_id
    assert await _synonym_states(document_id) == {"spelling": "active", "auto": "active"}


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
async def test_revival_without_a_synonym_another_entity_took(
    client: AsyncClient, auth_headers: dict, bulk: bool,
):
    national_id = "333333333" if not bulk else "444444444"
    [created] = await _post(client, auth_headers, [{"template_id": PERSON, "data": _person(national_id)}])
    document_id = created["document_id"]
    await _delete(client, auth_headers, document_id)

    holder = await _claim_elsewhere(_spelling(national_id))

    revived = await _revive(client, auth_headers, national_id, bulk=bulk)
    assert revived["status"] == "updated"
    assert revived["document_id"] == document_id
    assert any(holder in w for w in revived["warnings"]), revived["warnings"]
    assert await _synonym_states(document_id) == {"spelling": "inactive", "auto": "active"}


async def _set_status(document_id: str, status: str) -> None:
    """Change the document's row behind the Registry's back — the drift the
    reconcile pass exists for (a document retired before deletes told the
    Registry, or an undo that failed)."""
    await Document.find({"document_id": document_id}).update({"$set": {"status": status}})


@pytest.mark.asyncio
async def test_a_retirement_the_registry_never_recorded_is_reconciled(
    client: AsyncClient, auth_headers: dict,
):
    [created] = await _post(client, auth_headers, [{"template_id": PERSON, "data": _person("555555555")}])
    document_id = created["document_id"]
    await _set_status(document_id, "inactive")
    assert await _synonym_states(document_id) == {"spelling": "active", "auto": "active"}

    async def _reconcile(dry_run: bool) -> dict:
        response = await client.post(
            "/api/document-store/maintenance/reconcile-synonym-lifecycle",
            headers=auth_headers, json={"namespace": "wip", "dry_run": dry_run},
        )
        assert response.status_code == 200, response.text
        return response.json()

    dry = await _reconcile(dry_run=True)
    assert dry["retired_scanned"] == 1
    assert dry["released"] == 1
    assert dry["items"] == [{
        "entry_id": document_id, "status": "released",
        "keys": [_spelling("555555555")], "detail": None,
    }]
    assert await _synonym_states(document_id) == {"spelling": "active", "auto": "active"}

    applied = await _reconcile(dry_run=False)
    assert applied["released"] == 1
    assert await _synonym_states(document_id) == {"spelling": "inactive", "auto": "active"}

    again = await _reconcile(dry_run=False)
    assert again["released"] == 0
    assert again["unchanged"] == 1


@pytest.mark.asyncio
async def test_reconcile_revive_reclaims_an_active_documents_synonyms(
    client: AsyncClient, auth_headers: dict,
):
    [created] = await _post(client, auth_headers, [{"template_id": PERSON, "data": _person("666666666")}])
    document_id = created["document_id"]
    await _delete(client, auth_headers, document_id)
    await _set_status(document_id, "active")
    assert await _synonym_states(document_id) == {"spelling": "inactive", "auto": "active"}

    response = await client.post(
        "/api/document-store/maintenance/reconcile-synonym-lifecycle",
        headers=auth_headers, json={"namespace": "wip", "dry_run": False, "revive": True},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["reclaimed"] == 1
    assert await _synonym_states(document_id) == {"spelling": "active", "auto": "active"}


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
async def test_overwrite_in_place_revival_reclaims(client: AsyncClient, auth_headers: dict, bulk: bool):
    """A versioned: false template revives by overwriting in place — a
    separate branch on both write paths that must reach the Registry too."""
    note_id = "N930-single" if not bulk else "N930-bulk"
    [created] = await _post(client, auth_headers, [
        {"template_id": "LATEST_ONLY_NOTE", "data": {"note_id": note_id, "body": "first"}},
    ])
    document_id = created["document_id"]
    await _delete(client, auth_headers, document_id)
    assert (await _synonym_states(document_id))["spelling"] == "inactive"

    items = [{"template_id": "LATEST_ONLY_NOTE", "data": {"note_id": note_id, "body": "back"}}]
    if bulk:
        items.append({"template_id": PERSON, "data": _person("999999999", "Other")})
    revived = (await _post(client, auth_headers, items))[0]
    assert revived["status"] == "updated"
    assert revived["version"] == 1
    assert (await _synonym_states(document_id))["spelling"] == "active"


# ── Restore: retired entities' synonyms stay released ───────────────────────


def test_restore_claims_no_inactive_synonym():
    """An id-preserving restore inserts entries verbatim and derives their
    claims; an inactive synonym holds none."""
    from datetime import UTC, datetime

    rows = DirectRestoreEngine._claim_rows_for([{
        "entry_id": "E1", "namespace": "wip", "entity_type": "documents",
        "primary_composite_key_hash": "hash-primary",
        "synonyms": [
            {"namespace": "wip", "entity_type": "documents", "composite_key_hash": "hash-live"},
            {"namespace": "wip", "entity_type": "documents", "composite_key_hash": "hash-released",
             "status": "inactive"},
        ],
    }], datetime.now(UTC))
    assert [r["composite_key_hash"] for r in rows] == ["hash-primary", "hash-live"]


def _engine(events: list) -> DirectRestoreEngine:
    return DirectRestoreEngine(
        None, None, events.append,
        registry_base_url="http://registry", registry_api_key=os.environ["MASTER_API_KEY"],
    )


@pytest.mark.asyncio
async def test_restore_releases_retired_entities_synonyms(client: AsyncClient, auth_headers: dict, monkeypatch):
    """Rows arrive by direct writes, so no store saw the retirement; the
    restore releases the synonyms itself (archives taken before this change
    carry every synonym active)."""
    [created] = await _post(client, auth_headers, [{"template_id": PERSON, "data": _person("777777777")}])
    document_id = created["document_id"]
    events: list = []
    engine = _engine(events)

    async def _ids(namespace: str) -> list[str]:
        return [document_id]

    real_client = httpx.AsyncClient
    monkeypatch.setattr(engine, "_retired_entry_ids", _ids)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(
        transport=ASGITransport(app=registry_app), base_url="http://registry", **kw,
    ))
    await engine._release_retired_synonyms("wip")
    assert await _synonym_states(document_id) == {"spelling": "inactive", "auto": "active"}
    assert any("released the synonyms of 1" in e.message for e in events), events


@pytest.mark.asyncio
async def test_restore_release_failure_is_a_warning(monkeypatch):
    events: list = []
    engine = _engine(events)

    async def _unreadable(namespace: str) -> list[str]:
        raise RuntimeError("mongo down")

    monkeypatch.setattr(engine, "_retired_entry_ids", _unreadable)
    await engine._release_retired_synonyms("wip")
    assert [e.phase for e in events] == ["warning"]
    assert "reconcile-synonym-lifecycle" in events[0].message



@pytest.mark.asyncio
async def test_restore_release_item_failures_are_a_warning(monkeypatch):
    """The Registry answers per item inside a 200; items it could not release
    are named in a warning, not counted as nothing (CASE-1194, 3-R10)."""
    events: list = []
    engine = _engine(events)

    async def _ids(namespace: str) -> list[str]:
        return ["E1", "E2", "E3"]

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"results": [
                {"registry_id": "E1", "status": "deactivated"},
                {"registry_id": "E2", "status": "not_found"},
                {"registry_id": "E3", "status": "error", "error": "boom"},
            ]}

    class _Client:
        def __init__(self, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def post(self, *a, **kw): return _Resp()

    monkeypatch.setattr(engine, "_retired_entry_ids", _ids)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    await engine._release_retired_synonyms("wip")
    [warning] = [e for e in events if e.phase == "warning"]
    assert "2 retired entities could not be released" in warning.message
    assert "E2 (not_found" in warning.message and "E3 (error: boom)" in warning.message
    assert any("released the synonyms of 1" in e.message for e in events)
