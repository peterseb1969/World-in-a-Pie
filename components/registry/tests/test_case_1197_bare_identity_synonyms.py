"""A document's synonym may not be its identity values without "@template".

The restore refuses an archive holding such a synonym (it claims the
spelling for every template in the namespace), while the Registry wrote one
for any caller, after which every backup of the namespace failed to
restore. Every door that writes a synonym now refuses it, for every caller:
/synonyms/add, /entries/register and /entries/activate. Provenance:
CASE-1197.
"""

import uuid

import pytest
from httpx import AsyncClient

from registry.models.entry import RegistryEntry
from registry.models.namespace import Namespace
from tests.test_reservation_lifecycle import NAMESPACE, _provision
from tests.test_reserved_synonym_keys import _add, _writer_key

BASE = "/api/registry"


def _templates():
    return Namespace.get_motor_collection().database.client["wip_template_store"]["templates"]


async def _template(identity_fields: list[str]) -> str:
    template_id = f"tpl-1197-{uuid.uuid4().hex[:8]}"
    for version in (1, 2):
        await _templates().insert_one({
            "template_id": template_id, "namespace": NAMESPACE, "value": template_id.upper(),
            "version": version, "identity_fields": identity_fields,
        })
    return template_id


async def _document(client: AsyncClient, auth_headers: dict, template_id: str) -> str:
    resp = await client.post(f"{BASE}/entries/register", headers=auth_headers, json=[{
        "namespace": NAMESPACE, "entity_type": "documents",
        "composite_key": {"ns": NAMESPACE, "template_id": template_id},
    }])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]["registry_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["writer", "admin"])
async def test_add_refuses_the_bare_identity_values(client: AsyncClient, auth_headers: dict, caller):
    template_id = await _template(["code"])
    entry_id = await _document(client, auth_headers, template_id)
    headers = await _writer_key(client, auth_headers) if caller == "writer" else auth_headers

    result = await _add(client, headers, entry_id, {"code": "OTHER"})

    assert result["status"] == "error"
    assert result["error_code"] == "invalid_request"
    assert result["details"] == {
        "rule": "bare_identity_synonym", "template_id": template_id, "identity_fields": ["code"],
    }
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == entry_id)
    assert entry.synonyms == []


@pytest.mark.asyncio
async def test_add_still_takes_other_and_qualified_keys(client: AsyncClient, auth_headers: dict):
    template_id = await _template(["code", "region"])
    entry_id = await _document(client, auth_headers, template_id)
    writer = await _writer_key(client, auth_headers)

    # A subset or superset of the identity fields is another key, not them.
    for key in ({"code": "X"}, {"code": "X", "region": "EU", "erp": "1"}, {"erp_id": "E-9"}):
        assert (await _add(client, writer, entry_id, key))["status"] == "added", key
    qualified = {"@template": "T", "code": "X", "region": "EU"}
    assert (await _add(client, auth_headers, entry_id, qualified))["status"] == "added"


@pytest.mark.asyncio
async def test_add_does_not_judge_entries_that_are_not_documents(client: AsyncClient, auth_headers: dict):
    resp = await client.post(f"{BASE}/entries/register", headers=auth_headers, json=[{
        "namespace": NAMESPACE, "entity_type": "terms",
        "composite_key": {"ns": NAMESPACE, "template_id": await _template(["code"])},
    }])
    entry_id = resp.json()["results"][0]["registry_id"]
    resp = await client.post(f"{BASE}/synonyms/add", headers=auth_headers, json=[{
        "target_id": entry_id, "synonym_composite_key": {"code": "X"},
    }])
    assert resp.json()["results"][0]["status"] == "added"


@pytest.mark.asyncio
async def test_a_document_whose_template_is_unknown_is_not_refused(client: AsyncClient, auth_headers: dict):
    entry_id = await _document(client, auth_headers, f"tpl-missing-{uuid.uuid4().hex[:8]}")
    assert (await _add(client, auth_headers, entry_id, {"code": "X"}))["status"] == "added"


@pytest.mark.asyncio
async def test_register_refuses_bare_synonym_values(client: AsyncClient, auth_headers: dict):
    template_id = await _template(["code"])
    item = {
        "namespace": NAMESPACE, "entity_type": "documents",
        "composite_key": {"ns": NAMESPACE, "template_id": template_id},
        "identity_values": {"code": "C-1"},
    }
    resp = await client.post(f"{BASE}/entries/register", headers=auth_headers, json=[
        {**item, "synonym_values": {"code": "C-1"}},
        {**item, "identity_values": {"code": "C-2"},
         "synonym_values": {"@template": "T", "code": "C-2"}},
    ])
    bare, qualified = resp.json()["results"]
    assert bare["status"] == "error"
    assert bare["details"]["rule"] == "bare_identity_synonym"
    assert qualified["status"] == "created", qualified


@pytest.mark.asyncio
async def test_activate_refuses_a_bare_identity_synonym(client: AsyncClient, auth_headers: dict):
    template_id = await _template(["code"])
    (eid,) = await _provision(client, auth_headers, entity_type="documents", defer_claim=True)
    resp = await client.post(f"{BASE}/entries/activate", headers=auth_headers, json=[{
        "entry_id": eid, "composite_key": {"ns": NAMESPACE, "template_id": template_id},
        "synonyms": [{"synonym_composite_key": {"code": "A-1"}}],
    }])
    item = resp.json()["results"][0]
    assert item["status"] == "error"
    assert item["details"]["rule"] == "bare_identity_synonym"
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == eid)
    assert entry.status == "reserved"


# CASE-1263 (review of d1594bdd): the rule reaches every door and every
# template shape.


async def _child_template(parent_id: str) -> str:
    """A template that extends ``parent_id`` and declares no identity: its
    documents are hashed by the parent's fields."""
    template_id = f"tpl-child-{uuid.uuid4().hex[:8]}"
    await _templates().insert_one({
        "template_id": template_id, "namespace": NAMESPACE, "value": template_id.upper(),
        "version": 1, "identity_fields": [], "extends": parent_id, "extends_version": 1,
    })
    return template_id


@pytest.mark.asyncio
async def test_add_refuses_the_identity_a_template_inherits(client: AsyncClient, auth_headers: dict):
    child = await _child_template(await _template(["code"]))
    entry_id = await _document(client, auth_headers, child)

    result = await _add(client, auth_headers, entry_id, {"code": "X"})

    assert result["status"] == "error"
    assert result["details"]["identity_fields"] == ["code"]


@pytest.mark.asyncio
async def test_merge_refuses_a_key_that_is_bare_on_the_preferred_entry(
    client: AsyncClient, auth_headers: dict,
):
    """{"code": "X"} is an ordinary synonym on a document whose template is
    identified by "title"; merged onto a document identified by "code" it
    would be that document's bare identity."""
    by_code = await _document(client, auth_headers, await _template(["code"]))
    by_title = await _document(client, auth_headers, await _template(["title"]))
    assert (await _add(client, auth_headers, by_title, {"code": "X"}))["status"] == "added"

    resp = await client.post(f"{BASE}/synonyms/merge", headers=auth_headers, json=[{
        "preferred_id": by_code, "deprecated_id": by_title,
    }])

    result = resp.json()["results"][0]
    assert result["status"] == "error", result
    assert result["details"]["rule"] == "bare_identity_synonym"
    deprecated = await RegistryEntry.find_one(RegistryEntry.entry_id == by_title)
    assert deprecated.status == "active" and deprecated.merged_into is None


@pytest.mark.asyncio
async def test_an_unreadable_template_store_fails_the_item_not_the_request(
    client: AsyncClient, auth_headers: dict, monkeypatch,
):
    """The read decides nothing when it fails: a document synonym it would
    judge is an error of its own (never added unjudged), a system-qualified
    one and other items go through, and the request answers 200."""
    from registry.services import bare_identity

    entry_id = await _document(client, auth_headers, await _template(["code"]))

    async def broken(*_a, **_k):
        raise RuntimeError("template-store database unavailable")

    monkeypatch.setattr(bare_identity, "identity_fields_by_template", broken)
    resp = await client.post(f"{BASE}/synonyms/add", headers=auth_headers, json=[
        {"target_id": entry_id, "synonym_composite_key": {"code": "X"}},
        {"target_id": entry_id, "synonym_composite_key": {"@template": "T", "code": "Y"}},
    ])

    assert resp.status_code == 200
    judged, qualified = resp.json()["results"]
    assert judged["status"] == "error" and judged["error_code"] == "internal_error"
    assert judged["details"]["rule"] == "bare_identity_synonym"
    assert qualified["status"] == "added", qualified
