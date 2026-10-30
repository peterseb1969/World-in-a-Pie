"""A hard delete is refused while other documents still reference it.

References must resolve: hard-deleting a document that another document
references would leave that reference pointing at nothing, in whatever
namespace the referrer lives and whatever its own deletion_mode. The delete
is refused with invalid_state / rule=referenced unless force=true, which
deletes anyway and reports the referrers as broken_references. Documents
hard-deleted in the same request do not count, so a document and its edges
can go together. Deleting one version breaks only pinned references
recorded against that version; latest references still resolve.
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from document_store.models.document import Document
from registry.main import app as registry_app

DOCS = "/api/document-store/documents"
FULL = "hd-full"
RETAIN = "hd-retain"


async def _namespace(headers: dict, prefix: str, body: dict) -> None:
    async with AsyncClient(transport=ASGITransport(app=registry_app), base_url="http://registry") as reg:
        resp = await reg.put(f"/api/registry/namespaces/{prefix}", headers=headers, json=body)
    assert resp.status_code == 200, resp.text


async def _post(client: AsyncClient, headers: dict, items: list[dict]) -> list[dict]:
    resp = await client.post(DOCS, headers=headers, json=items)
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert all(r["status"] in ("created", "updated") for r in results), results
    return results


async def _delete(client: AsyncClient, headers: dict, items: list[dict]) -> list[dict]:
    resp = await client.request("DELETE", DOCS, headers=headers, json=items)
    assert resp.status_code == 200, resp.text
    return sorted(resp.json()["results"], key=lambda r: r["index"])


def _chapter(namespace: str = FULL) -> dict:
    return {"template_id": "CHAPTER_Q", "namespace": namespace,
            "data": {"title": f"C-{uuid.uuid4().hex[:8]}"}}


def _holder(ref: str, namespace: str = FULL) -> dict:
    return {"template_id": "HOLDER_Q", "namespace": namespace,
            "data": {"name": f"H-{uuid.uuid4().hex[:8]}", "ref": ref}}


@pytest.fixture
async def namespaces(auth_headers: dict) -> None:
    await _namespace(auth_headers, FULL, {"description": "hard-delete tests", "deletion_mode": "full"})
    await _namespace(auth_headers, RETAIN, {
        "description": "hard-delete tests", "allowed_external_refs": [FULL],
    })


async def _chapter_and_holder(client, headers, holder_ns: str = FULL) -> tuple[str, str]:
    [chapter] = await _post(client, headers, [_chapter()])
    [holder] = await _post(client, headers, [_holder(chapter["document_id"], holder_ns)])
    return chapter["document_id"], holder["document_id"]


async def _exists(document_id: str) -> bool:
    return await Document.find_one({"document_id": document_id}) is not None


@pytest.mark.asyncio
async def test_a_referenced_document_is_not_hard_deleted(client, auth_headers, namespaces):
    chapter, holder = await _chapter_and_holder(client, auth_headers)

    [item] = await _delete(client, auth_headers, [{"id": chapter, "hard_delete": True}])

    assert item["status"] == "error"
    assert item["error_code"] == "invalid_state"
    assert item["details"]["rule"] == "referenced"
    [group] = item["details"]["referrers"]
    assert group["namespace"] == FULL
    assert group["documents"] == 1
    assert group["sample_document_ids"] == [holder]
    assert await _exists(chapter)


@pytest.mark.asyncio
async def test_a_referrer_in_another_namespace_blocks(client, auth_headers, namespaces):
    chapter, holder = await _chapter_and_holder(client, auth_headers, holder_ns=RETAIN)

    [item] = await _delete(client, auth_headers, [{"id": chapter, "hard_delete": True}])

    assert item["details"]["rule"] == "referenced"
    [group] = item["details"]["referrers"]
    assert group["namespace"] == RETAIN
    assert group["sample_document_ids"] == [holder]
    assert await _exists(chapter)


@pytest.mark.asyncio
async def test_a_retired_referrer_blocks(client, auth_headers, namespaces):
    chapter, holder = await _chapter_and_holder(client, auth_headers)
    [soft] = await _delete(client, auth_headers, [{"id": holder}])
    assert soft["status"] == "deleted"

    [item] = await _delete(client, auth_headers, [{"id": chapter, "hard_delete": True}])

    assert item["details"]["rule"] == "referenced"


@pytest.mark.asyncio
async def test_an_unreferenced_document_is_hard_deleted(client, auth_headers, namespaces):
    [chapter] = await _post(client, auth_headers, [_chapter()])

    [item] = await _delete(client, auth_headers, [{"id": chapter["document_id"], "hard_delete": True}])

    assert item["status"] == "deleted", item
    assert item["details"] is None
    assert not await _exists(chapter["document_id"])


@pytest.mark.asyncio
async def test_force_deletes_and_reports_the_broken_references(client, auth_headers, namespaces):
    chapter, holder = await _chapter_and_holder(client, auth_headers, holder_ns=RETAIN)

    [item] = await _delete(client, auth_headers, [{"id": chapter, "hard_delete": True, "force": True}])

    assert item["status"] == "deleted", item
    [group] = item["details"]["broken_references"]
    assert group["namespace"] == RETAIN
    assert group["sample_document_ids"] == [holder]
    assert any("no longer resolve" in w for w in item["warnings"])
    assert not await _exists(chapter)


@pytest.mark.asyncio
async def test_force_without_hard_delete_is_still_refused(client, auth_headers, namespaces):
    chapter, _ = await _chapter_and_holder(client, auth_headers)

    [item] = await _delete(client, auth_headers, [{"id": chapter, "force": True}])

    assert item["error_code"] == "force_unsupported"


@pytest.mark.asyncio
@pytest.mark.parametrize("referrer_first", [True, False])
async def test_referrers_deleted_in_the_same_request_do_not_block(
    client, auth_headers, namespaces, referrer_first,
):
    chapter, holder = await _chapter_and_holder(client, auth_headers)
    items = [{"id": chapter, "hard_delete": True}, {"id": holder, "hard_delete": True}]
    if referrer_first:
        items.reverse()

    results = await _delete(client, auth_headers, items)

    assert [r["status"] for r in results] == ["deleted", "deleted"], results
    assert not await _exists(chapter)
    assert not await _exists(holder)


@pytest.mark.asyncio
async def test_a_referrer_soft_deleted_in_the_same_request_still_blocks(client, auth_headers, namespaces):
    chapter, holder = await _chapter_and_holder(client, auth_headers)

    results = await _delete(client, auth_headers, [
        {"id": holder}, {"id": chapter, "hard_delete": True},
    ])

    assert results[0]["status"] == "deleted"
    assert results[1]["details"]["rule"] == "referenced"


@pytest.mark.asyncio
async def test_a_self_reference_does_not_block(client, auth_headers, namespaces):
    [created] = await _post(client, auth_headers, [_chapter()])
    chapter = created["document_id"]
    # Point the chapter's own row at itself, as a self-edge would.
    await Document.get_motor_collection().update_many(
        {"document_id": chapter},
        {"$set": {"references": [{"field_path": "ref", "reference_type": "document",
                                  "version_strategy": "latest",
                                  "resolved": {"document_id": chapter, "version": 1}}]}},
    )

    [item] = await _delete(client, auth_headers, [{"id": chapter, "hard_delete": True}])

    assert item["status"] == "deleted", item


async def _molecule_with_two_versions(client, headers) -> str:
    mol_id = f"MOL-{uuid.uuid4().hex[:8]}"
    [v1] = await _post(client, headers, [{"template_id": "MOLECULE", "namespace": FULL,
                                          "data": {"molecule_id": mol_id, "name": "one"}}])
    [v2] = await _post(client, headers, [{"template_id": "MOLECULE", "namespace": FULL,
                                          "data": {"molecule_id": mol_id, "name": "two"}}])
    assert v2["document_id"] == v1["document_id"] and v2["version"] == 2, v2
    return v1["document_id"]


async def _holder_pointing_at(client, headers, target: str, strategy: str, version: int) -> str:
    """A referrer whose stored reference has the given strategy and version."""
    [chapter] = await _post(client, headers, [_chapter()])
    [holder] = await _post(client, headers, [_holder(chapter["document_id"])])
    await Document.get_motor_collection().update_many(
        {"document_id": holder["document_id"]},
        {"$set": {"references.0.resolved.document_id": target,
                  "references.0.resolved.version": version,
                  "references.0.version_strategy": strategy}},
    )
    return holder["document_id"]


@pytest.mark.asyncio
async def test_deleting_one_version_keeps_latest_references(client, auth_headers, namespaces):
    molecule = await _molecule_with_two_versions(client, auth_headers)
    await _holder_pointing_at(client, auth_headers, molecule, "latest", 1)

    [item] = await _delete(client, auth_headers, [{"id": molecule, "hard_delete": True, "version": 1}])

    assert item["status"] == "deleted", item


@pytest.mark.asyncio
async def test_deleting_a_pinned_version_is_refused(client, auth_headers, namespaces):
    molecule = await _molecule_with_two_versions(client, auth_headers)
    holder = await _holder_pointing_at(client, auth_headers, molecule, "pinned", 1)

    [refused] = await _delete(client, auth_headers, [{"id": molecule, "hard_delete": True, "version": 1}])
    [other] = await _delete(client, auth_headers, [{"id": molecule, "hard_delete": True, "version": 2}])

    assert refused["details"]["rule"] == "referenced"
    assert refused["details"]["referrers"][0]["sample_document_ids"] == [holder]
    assert other["status"] == "deleted", other


@pytest.mark.asyncio
async def test_deleting_the_last_version_counts_every_reference(client, auth_headers, namespaces):
    chapter, _ = await _chapter_and_holder(client, auth_headers)

    [item] = await _delete(client, auth_headers, [{"id": chapter, "hard_delete": True, "version": 1}])

    assert item["details"]["rule"] == "referenced"
    assert await _exists(chapter)


@pytest.mark.asyncio
async def test_referrers_in_an_unreadable_namespace_are_counted_without_ids(
    client, auth_headers, namespaces, monkeypatch,
):
    import wip_auth

    chapter, _ = await _chapter_and_holder(client, auth_headers, holder_ns=RETAIN)

    async def no_read_on_retain(identity, namespace):
        return "none" if namespace == RETAIN else "admin"

    monkeypatch.setattr(wip_auth, "resolve_permission", no_read_on_retain)

    [item] = await _delete(client, auth_headers, [{"id": chapter, "hard_delete": True}])

    [group] = item["details"]["referrers"]
    assert group == {"namespace": RETAIN, "template_id": group["template_id"], "documents": 1}
