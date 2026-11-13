"""Document-store core fixes from one code review.

Provenance: CASE-1144. Each test pins one behaviour:

- a bulk create registers the template-scoped auto-synonym a single create
  registers, and fails (and removes) a document whose synonym is refused;
- GET /documents/by-identity resolves template_id synonyms, answers the latest
  version, and refuses to pick between several documents;
- a migrate dry run reports the document version the apply would write;
- a create/upsert result names the template version the document is pinned to;
- validate-candidate classifies a dotted identity path into a nested template;
- a version pinned by the caller that is already taken is an error, never a
  bumped version that retires the document's current head;
- a document_id without a version must be the document the identity resolves
  to, on the single and the bulk door alike;
- PATCH refuses an identity change, not another spelling of the same referent.
"""

import copy
import uuid

import pytest

from document_store.models.document import Document, DocumentStatus
from document_store.services.registry_client import RegistryClient
from registry.models.entry import RegistryEntry

from .conftest import SAMPLE_TEMPLATES, VERSIONED_TEMPLATE_OVERRIDES

DOCS = "/api/document-store/documents"
MIGRATE = "/api/document-store/documents/migrate"
CANDIDATE = "/api/document-store/validation/validate-candidate"


@pytest.fixture(autouse=True)
def _clear_overrides():
    VERSIONED_TEMPLATE_OVERRIDES.clear()
    yield
    VERSIONED_TEMPLATE_OVERRIDES.clear()


def _person(national_id: str, first_name: str = "Ada") -> dict:
    return {"national_id": national_id, "first_name": first_name, "last_name": "Core"}


async def _post(client, auth_headers, items: list[dict]) -> list[dict]:
    resp = await client.post(DOCS, json=items, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


def _item(data: dict, template_id: str = "PERSON", **extra) -> dict:
    return {"template_id": template_id, "namespace": "wip", "data": data, **extra}


async def _auto_synonym_keys(document_id: str) -> list[dict]:
    entry = await RegistryEntry.find_one({"entry_id": document_id})
    assert entry is not None
    return [
        s.composite_key for s in entry.synonyms
        if s.composite_key.get("type") == "document"
    ]


async def _rows(document_id: str) -> list[Document]:
    return await Document.find({"document_id": document_id}).sort("version").to_list()


# --- bulk auto-synonym -----------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_create_registers_the_auto_synonym(client, auth_headers):
    single = (await _post(client, auth_headers, [_item(_person("114400001"))]))[0]
    bulk = await _post(client, auth_headers, [
        _item(_person("114400002")), _item(_person("114400003")),
    ])
    assert [r["status"] for r in bulk] == ["created", "created"]

    for result in [single, *bulk]:
        keys = await _auto_synonym_keys(result["document_id"])
        assert {
            "ns": "wip", "type": "document", "template": "PERSON",
            "identity_hash": result["identity_hash"],
        } in keys, keys


@pytest.mark.asyncio
async def test_bulk_create_fails_a_document_whose_auto_synonym_is_refused(
    client, auth_headers, monkeypatch,
):
    real = RegistryClient.register_auto_synonyms_per_item

    async def refuse_first(self, documents, namespace, created_by=None):
        out = await real(self, documents[1:], namespace, created_by)
        return ["Failed to register auto-synonym: status=error", *out]

    monkeypatch.setattr(RegistryClient, "register_auto_synonyms_per_item", refuse_first)
    results = await _post(client, auth_headers, [
        _item(_person("114400011")), _item(_person("114400012")),
    ])
    assert results[0]["status"] == "error"
    assert results[0]["error_code"] == "registry_error"
    assert results[1]["status"] == "created"
    assert await Document.find(
        {"data.national_id": "114400011"}
    ).count() == 0
    assert await Document.find(
        {"data.national_id": "114400012"}
    ).count() == 1


# --- by-identity -----------------------------------------------------------


@pytest.mark.asyncio
async def test_by_identity_resolves_a_template_synonym(client, auth_headers):
    created = (await _post(client, auth_headers, [_item(_person("114400021"))]))[0]
    resp = await client.get(
        f"{DOCS}/by-identity/{created['identity_hash']}",
        params={"namespace": "wip", "template_id": "PERSON"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["document_id"] == created["document_id"]


@pytest.mark.asyncio
async def test_by_identity_with_inactive_answers_the_latest_version(client, auth_headers):
    first = (await _post(client, auth_headers, [_item(_person("114400031"))]))[0]
    await _post(client, auth_headers, [_item(_person("114400031", "Bea"))])
    await _post(client, auth_headers, [_item(_person("114400031", "Cy"))])

    resp = await client.get(
        f"{DOCS}/by-identity/{first['identity_hash']}",
        params={"namespace": "wip", "template_id": "PERSON", "include_inactive": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == 3


@pytest.mark.asyncio
async def test_by_identity_refuses_to_pick_between_documents(client, auth_headers):
    created = (await _post(client, auth_headers, [_item(_person("114400041"))]))[0]
    (row,) = await _rows(created["document_id"])
    other_template = SAMPLE_TEMPLATES["TERM_IDENTITY"]["template_id"]
    twin = row.model_copy(update={
        "id": None, "document_id": str(uuid.uuid4()), "template_id": other_template,
        "template_value": "TERM_IDENTITY",
    })
    await twin.insert()

    resp = await client.get(
        f"{DOCS}/by-identity/{created['identity_hash']}",
        params={"namespace": "wip"},
        headers=auth_headers,
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    # The platform's shape for an identifier naming several entities.
    assert (detail["error"], detail["error_code"]) == ("ambiguous", "ambiguous_reference")
    assert detail["identifier"] == created["identity_hash"] and detail["total"] == 2
    assert {c["document_id"] for c in detail["candidates"]} == {
        created["document_id"], twin.document_id,
    }

    resp = await client.get(
        f"{DOCS}/by-identity/{created['identity_hash']}",
        params={"namespace": "wip", "template_id": "PERSON"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["document_id"] == created["document_id"]


# --- migrate dry run --------------------------------------------------------


@pytest.mark.asyncio
async def test_migrate_dry_run_reports_the_document_version_the_apply_writes(
    client, auth_headers,
):
    await _post(client, auth_headers, [_item(_person("114400051"))])
    await _post(client, auth_headers, [_item(_person("114400051", "Bea"))])  # doc v2
    tid = SAMPLE_TEMPLATES["PERSON"]["template_id"]
    v2 = copy.deepcopy(SAMPLE_TEMPLATES[tid])
    v2["version"] = 2
    VERSIONED_TEMPLATE_OVERRIDES[(tid, 2)] = v2

    body = {"template_id": "PERSON", "from_version": 1, "to_version": 2}
    dry = await client.post(
        MIGRATE, params={"namespace": "wip"}, json={**body, "dry_run": True},
        headers=auth_headers,
    )
    assert dry.status_code == 200, dry.text
    applied = await client.post(
        MIGRATE, params={"namespace": "wip"}, json={**body, "dry_run": False},
        headers=auth_headers,
    )
    assert applied.status_code == 200, applied.text
    dry_item = dry.json()["results"][0]
    applied_item = applied.json()["results"][0]
    assert dry_item["version"] == applied_item["version"] == 3
    assert dry_item["template_version"] == applied_item["template_version"] == 2


# --- template_version on the write result ----------------------------------


@pytest.mark.asyncio
async def test_create_results_name_the_pinned_template_version(client, auth_headers):
    single = (await _post(client, auth_headers, [_item(_person("114400061"))]))[0]
    bulk = await _post(client, auth_headers, [
        _item(_person("114400062")), _item(_person("114400061")),
    ])
    assert single["template_version"] == 1
    assert [r["status"] for r in bulk] == ["created", "unchanged"]
    assert [r["template_version"] for r in bulk] == [1, 1]


# --- validate-candidate, nested identity path -------------------------------


@pytest.mark.asyncio
async def test_candidate_classifies_a_nested_identity_path(client, auth_headers):
    candidate = copy.deepcopy(SAMPLE_TEMPLATES["NESTED_TERM_IDENTITY"])
    # A candidate exists nowhere, so it never carries the stored answer; one
    # the caller sends is not trusted either.
    candidate["referent_identity_paths"] = []
    resp = await client.post(
        CANDIDATE,
        json={
            "namespace": "wip",
            "template_definition": candidate,
            "documents": [
                {"name": "Ada", "address": {"country": "UK"}},
                {"name": "Ada", "address": {"country": "uk"}},
            ],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    results = [r["validation"] for r in resp.json()["results"]]
    assert all(r["valid"] for r in results), results
    # Two aliases of one term at the nested path are one identity.
    assert results[0]["identity_hash"] == results[1]["identity_hash"]

    del candidate["referent_identity_paths"]
    resp = await client.post(
        CANDIDATE,
        json={"namespace": "wip", "template_definition": candidate,
              "documents": [{"name": "Ada", "address": {"country": "CA"}}]},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["validation"]["valid"], resp.json()


# --- pinned version collision -----------------------------------------------


async def _three_versions_without_entry(client, auth_headers, national_id: str) -> str:
    """A document at v3 whose Registry entry is gone, so a pinned write
    reaches the Mongo version slot instead of the Registry's id check."""
    first = (await _post(client, auth_headers, [_item(_person(national_id))]))[0]
    await _post(client, auth_headers, [_item(_person(national_id, "Bea"))])
    await _post(client, auth_headers, [_item(_person(national_id, "Cy"))])
    await RegistryEntry.find({"entry_id": first["document_id"]}).delete()
    return first["document_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True])
async def test_a_taken_pinned_version_is_refused(client, auth_headers, bulk):
    national_id = "11440007" + ("2" if bulk else "1")
    doc_id = await _three_versions_without_entry(client, auth_headers, national_id)
    items = [_item(_person(national_id, "Dee"), document_id=doc_id, version=2)]
    if bulk:
        items.append(_item(_person("114400079")))
    result = (await _post(client, auth_headers, items))[0]

    assert result["status"] == "error", result
    assert result["error_code"] == "already_exists"
    rows = await _rows(doc_id)
    assert [r.version for r in rows] == [1, 2, 3]
    assert [r.status for r in rows][-1] == DocumentStatus.ACTIVE


# --- document_id without version --------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True])
async def test_a_document_id_the_identity_does_not_name_is_refused(
    client, auth_headers, bulk,
):
    national_id = "11440008" + ("2" if bulk else "1")
    stray_id = str(uuid.uuid4())
    items = [_item(_person(national_id), document_id=stray_id)]
    entries_extra = 0
    if bulk:
        items.append(_item(_person("114400089")))
        entries_extra = 1
    entries_before = await RegistryEntry.find({"entity_type": "documents"}).count()
    result = (await _post(client, auth_headers, items))[0]

    assert result["status"] == "error", result
    assert result["error_code"] == "invalid_request"
    assert result["details"]["rule"] == "document_id_mismatch"
    assert await Document.find({"data.national_id": national_id}).count() == 0
    # The entry minted for the new identity is released, not left behind
    # (the bulk case's second, valid item keeps its own).
    assert await RegistryEntry.find(
        {"entity_type": "documents"}
    ).count() == entries_before + entries_extra


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True])
async def test_reposting_a_document_with_its_own_id_updates_it(
    client, auth_headers, bulk,
):
    national_id = "11440009" + ("2" if bulk else "1")
    first = (await _post(client, auth_headers, [_item(_person(national_id))]))[0]
    items = [_item(_person(national_id, "Bea"), document_id=first["document_id"])]
    if bulk:
        items.append(_item(_person("114400099")))
    result = (await _post(client, auth_headers, items))[0]

    assert result["status"] == "updated", result
    assert result["document_id"] == first["document_id"]
    assert result["version"] == 2


# --- PATCH identity guard ----------------------------------------------------


async def _patch(client, auth_headers, document_id: str, patch: dict) -> dict:
    resp = await client.patch(
        DOCS, json=[{"document_id": document_id, "patch": patch}], headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


@pytest.mark.asyncio
async def test_patch_accepts_another_alias_of_the_identity_term(client, auth_headers):
    created = (await _post(client, auth_headers, [
        _item({"category": "M", "note": "a"}, template_id="TERM_IDENTITY"),
    ]))[0]
    result = await _patch(client, auth_headers, created["document_id"], {"category": "m"})
    assert result["status"] == "updated", result

    result = await _patch(client, auth_headers, created["document_id"], {"category": "F"})
    assert result["status"] == "error"
    assert result["error_code"] == "identity_field_change"


@pytest.mark.asyncio
async def test_patch_without_a_stored_resolution_does_not_claim_another_entity(
    client, auth_headers,
):
    """A version stored without its identity term's resolution can only be
    compared by spelling. The refusal says so instead of asserting that the
    respelling names a different entity, which was never established."""
    from document_store.models.document import Document

    created = (await _post(client, auth_headers, [
        _item({"category": "M", "note": "a"}, template_id="TERM_IDENTITY"),
    ]))[0]
    await Document.get_motor_collection().update_many(
        {"document_id": created["document_id"]}, {"$set": {"term_references": []}},
    )
    result = await _patch(client, auth_headers, created["document_id"], {"category": "m"})
    assert result["status"] == "error" and result["error_code"] == "identity_field_change"
    assert "holds no resolution" in result["error"], result
    assert "names a different entity" not in result["error"], result
