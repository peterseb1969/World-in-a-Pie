"""The identity-hash migration's apply converges after a failed Registry leg.

The apply writes Mongo first, then re-keys the Registry claims. When the
Registry leg failed, the Mongo rows already carried the new hash, so a re-run
saw those documents as unchanged and never asked the Registry again: their
claims stayed on the old hash, `applied` had counted them as done, and no
later run said otherwise. Every apply now re-sends the re-key for documents
whose stored hash is already right (the Registry answers `unchanged` for a
current claim), counts Registry failures separately from `applied`, and sends
the re-key in bounded batches.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from document_store.models.document import Document
from document_store.services import identity_migration
from document_store.services.registry_client import RegistryError
from wip_auth.document_identity import compute_hash

PATCH_REGISTRY = "document_store.services.identity_migration.get_registry_client"


async def _create(client: AsyncClient, auth_headers: dict, category: str) -> dict:
    response = await client.post(
        "/api/document-store/documents", headers=auth_headers,
        json=[{"namespace": "wip", "template_id": "TERM_IDENTITY",
               "data": {"category": category}}],
    )
    assert response.status_code == 200, response.text
    item = response.json()["results"][0]
    assert item["status"] == "created", item
    return item


async def _forge_legacy(doc_id: str, category: str) -> str:
    legacy = compute_hash({"category": category})
    await Document.get_motor_collection().update_many(
        {"document_id": doc_id}, {"$set": {"identity_hash": legacy}},
    )
    return legacy


async def _apply(client: AsyncClient, auth_headers: dict) -> dict:
    response = await client.post(
        "/api/document-store/maintenance/rehash-identities", headers=auth_headers,
        json={"namespace": "wip", "dry_run": False, "template_values": ["TERM_IDENTITY"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


class _Registry:
    """A Registry stand-in recording every re-key request."""

    def __init__(self, fail_batches: set[int] | None = None, status: str = "rekeyed"):
        self.calls: list[list[dict]] = []
        self.fail_batches = fail_batches or set()
        self.status = status

    async def rekey_identity(self, items: list[dict]) -> list[dict]:
        self.calls.append(items)
        if len(self.calls) - 1 in self.fail_batches:
            raise RegistryError("simulated registry outage")
        return [{"entry_id": i["entry_id"], "status": self.status} for i in items]

    def sent_ids(self) -> list[str]:
        return [i["entry_id"] for call in self.calls for i in call]


@pytest.mark.asyncio
async def test_failed_registry_leg_is_re_sent_by_the_next_apply(
    client: AsyncClient, auth_headers: dict,
):
    created = await _create(client, auth_headers, "M")
    doc_id, resolved = created["document_id"], created["identity_hash"]
    await _forge_legacy(doc_id, "M")

    # First apply: Mongo moves, the Registry leg fails.
    with patch(PATCH_REGISTRY, return_value=_Registry(fail_batches={0})):
        report = await _apply(client, auth_headers)
    mine = next(i for i in report["items"] if i["document_id"] == doc_id)
    assert mine["status"] == "changed"
    assert mine["registry_status"].startswith("error: simulated registry outage")
    assert report["applied"] == 0, "a failed Registry leg is not a completed migration"
    assert report["registry_failed"] == 1
    row = await Document.get_motor_collection().find_one({"document_id": doc_id})
    assert row["identity_hash"] == resolved

    # Second apply: Mongo is already right, and the re-key is sent again.
    registry = _Registry()
    with patch(PATCH_REGISTRY, return_value=registry):
        report = await _apply(client, auth_headers)
    assert doc_id in registry.sent_ids(), "the re-key must be re-sent"
    sent = next(i for c in registry.calls for i in c if i["entry_id"] == doc_id)
    assert sent["identity_hash"] == resolved
    mine = next(i for i in report["items"] if i["document_id"] == doc_id)
    assert mine["status"] == "unchanged"
    assert mine["registry_status"] == "rekeyed"
    assert report["registry_repaired"] == 1
    assert report["registry_failed"] == 0
    assert report["applied"] == 0, "no Mongo rows were rewritten by this run"


@pytest.mark.asyncio
async def test_re_sent_re_key_is_unchanged_against_the_real_registry(
    client: AsyncClient, auth_headers: dict,
):
    # The real Registry endpoint answers `unchanged` for a claim that is
    # already current, so re-sending on every apply is safe and quiet.
    created = await _create(client, auth_headers, "F")
    doc_id = created["document_id"]
    await _forge_legacy(doc_id, "F")

    first = await _apply(client, auth_headers)
    mine = next(i for i in first["items"] if i["document_id"] == doc_id)
    assert mine["registry_status"] in ("rekeyed", "unchanged"), mine
    assert first["applied"] >= 1 and first["registry_failed"] == 0

    second = await _apply(client, auth_headers)
    assert all(i["document_id"] != doc_id for i in second["items"]), (
        "a document whose hash and claim are both current is not listed"
    )
    assert second["registry_failed"] == 0
    assert second["registry_repaired"] == 0


@pytest.mark.asyncio
async def test_re_key_is_batched_and_a_failed_batch_marks_only_its_own(
    client: AsyncClient, auth_headers: dict, monkeypatch,
):
    monkeypatch.setattr(identity_migration, "REKEY_BATCH_SIZE", 2)
    docs = []
    for category in ("M", "F", "O"):
        created = await _create(client, auth_headers, category)
        await _forge_legacy(created["document_id"], category)
        docs.append(created["document_id"])

    registry = _Registry(fail_batches={0})
    with patch(PATCH_REGISTRY, return_value=registry):
        report = await _apply(client, auth_headers)

    assert len(registry.calls) == 2, registry.calls
    assert all(len(call) <= 2 for call in registry.calls)
    statuses = {i["document_id"]: i["registry_status"] for i in report["items"]}
    first_batch = {i["entry_id"] for i in registry.calls[0]}
    for doc_id in docs:
        if doc_id in first_batch:
            assert statuses[doc_id].startswith("error:"), statuses
        else:
            assert statuses[doc_id] == "rekeyed", statuses
    assert report["registry_failed"] == len(first_batch)
    assert report["applied"] == 3 - len(first_batch)


@pytest.mark.asyncio
async def test_registry_conflict_is_a_failure_with_its_reason(
    client: AsyncClient, auth_headers: dict,
):
    created = await _create(client, auth_headers, "O")
    doc_id = created["document_id"]
    await _forge_legacy(doc_id, "O")

    class _Conflicting(_Registry):
        async def rekey_identity(self, items):
            self.calls.append(items)
            return [{"entry_id": i["entry_id"], "status": "conflict",
                     "error": "target key is held by entry 'x'"} for i in items]

    with patch(PATCH_REGISTRY, return_value=_Conflicting()):
        report = await _apply(client, auth_headers)
    mine = next(i for i in report["items"] if i["document_id"] == doc_id)
    assert mine["registry_status"] == "conflict"
    assert "held by entry" in mine["detail"]
    assert report["registry_failed"] == 1 and report["applied"] == 0
