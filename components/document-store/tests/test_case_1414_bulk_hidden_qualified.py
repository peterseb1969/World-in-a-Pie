"""A bulk item naming a hidden document or file by a qualified id answers
exactly as one naming nothing.

Provenance: CASE-1414 (review of f16279e9, R1). A bulk door resolves each
item's id in place before it checks read: a qualified ``ns:VALUE`` resolves by
its own prefix, so for an entity in a namespace the caller cannot read the item
reached the door holding the canonical id, and the not_found answer named it,
while an unknown ``ns:VALUE`` was answered as submitted. The difference
confirmed that the entity exists and gave its id away. Each door now answers
its misses with the ids as submitted.

The key is single-namespace ('wip'), so the bulk doors resolve; 'xns-closed'
is invisible to it.
"""

from __future__ import annotations

import os
import uuid

import pytest
from httpx import AsyncClient

import tests.test_files as test_files
from document_store.models.document import Document, DocumentStatus
from document_store.models.file import File, FileStatus
from tests.conftest import SAMPLE_TEMPLATES
from tests.test_files import file_client  # noqa: F401 — fixture

HIDDEN_NS = "xns-closed"
APP_KEY = {"X-API-Key": "test_xns_writer_key"}  # 'wip' only
API = "/api/document-store"

DOORS = {
    "patch documents": ("PATCH", "/documents", [{"document_id": "{id}", "patch": {"first_name": "x"}}], "document"),
    "delete documents": ("DELETE", "/documents", [{"id": "{id}"}], "document"),
    "patch files": ("PATCH", "/files", [{"file_id": "{id}", "description": "x"}], "file"),
    "delete files": ("DELETE", "/files", [{"id": "{id}"}], "file"),
    "reactivate files": ("POST", "/files/reactivate", [{"id": "{id}"}], "file"),
}


async def _registry(path: str, body: list) -> dict:
    headers = {"X-API-Key": os.environ["MASTER_API_KEY"]}
    async with AsyncClient(transport=test_files._registry_transport, base_url="http://registry") as client:
        resp = await client.post(path, headers=headers, json=body)
    assert resp.status_code == 200, resp.text
    result = resp.json()["results"][0]
    assert result["status"] in ("created", "added"), result
    return result


async def _hidden(kind: str, template: str = "PERSON") -> tuple[str, str]:
    """A document, file or template entry in the hidden namespace,
    addressable as ns:VALUE. Returns (canonical id, value)."""
    value = f"HID_{uuid.uuid4().hex[:8].upper()}"
    entry = await _registry("/api/registry/entries/register", [{
        "namespace": HIDDEN_NS, "entity_type": f"{kind}s",
        "composite_key": {"probe": uuid.uuid4().hex},
    }])
    entity_id = entry["registry_id"]
    await _registry("/api/registry/synonyms/add", [{
        "target_id": entity_id, "synonym_composite_key": {"value": value},
    }])
    if kind == "document":
        await Document(
            namespace=HIDDEN_NS, document_id=entity_id,
            template_id=SAMPLE_TEMPLATES[template]["template_id"], template_value=template,
            template_version=1, identity_hash=f"hidden-{entity_id}", version=1,
            data={"first_name": "Hidden", "national_id": value}, status=DocumentStatus.ACTIVE,
        ).insert()
    elif kind == "file":
        await File(
            namespace=HIDDEN_NS, file_id=entity_id, filename="hidden.txt",
            content_type="text/plain", size_bytes=1, checksum=uuid.uuid4().hex * 2,
            storage_key=entity_id, status=FileStatus.INACTIVE,
        ).insert()
    return entity_id, value


async def _call(client: AsyncClient, door: str, entity_id: str):
    method, path, body, _ = DOORS[door]
    payload = [{k: (v.format(id=entity_id) if isinstance(v, str) else v) for k, v in item.items()} for item in body]
    return await client.request(method, API + path, headers=APP_KEY, json=payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("door", list(DOORS))
async def test_a_hidden_qualified_id_answers_as_an_unknown_one(file_client: AsyncClient, door: str):  # noqa: F811
    canonical, value = await _hidden(DOORS[door][3])
    hidden_id = f"{HIDDEN_NS}:{value}"
    missing_id = f"{HIDDEN_NS}:NOPE_{uuid.uuid4().hex[:8].upper()}"

    hidden = await _call(file_client, door, hidden_id)
    missing = await _call(file_client, door, missing_id)

    assert missing.status_code == 200, missing.text  # the key is in effect
    assert missing.json()["results"][0]["error_code"] == "not_found", missing.text
    assert canonical not in hidden.text, hidden.text  # the canonical id is not given away
    assert hidden.text.replace(hidden_id, "<id>") == missing.text.replace(missing_id, "<id>")


# A value several entities of a hidden namespace bear: the ambiguity answer
# lists its candidates (canonical ids, the keys that carried the value), so it
# must read as a miss to a caller that cannot read them, on the single doors
# (resolve_or_404) and the bulk doors (resolve_bulk_ids) alike.
AMBIGUITY_DOORS = {
    **DOORS,
    "get document": ("GET", "/documents/{id}", None, "document"),
    "get file": ("GET", "/files/{id}", None, "file"),
}


async def _ambiguous_value(kind: str) -> tuple[list[str], str]:
    """Two entities of the hidden namespace bearing one value under two keys."""
    first, value = await _hidden(kind)
    second, _ = await _hidden(kind)
    await _registry("/api/registry/synonyms/add", [{
        "target_id": second, "synonym_composite_key": {"alias": value},
    }])
    return [first, second], value


async def _ambiguity_call(client: AsyncClient, door: str, entity_id: str):
    method, path, body, _ = AMBIGUITY_DOORS[door]
    if body is None:
        return await client.request(method, API + path.format(id=entity_id), headers=APP_KEY)
    return await _call(client, door, entity_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("door", list(AMBIGUITY_DOORS))
async def test_a_hidden_ambiguous_value_answers_as_an_unknown_one(file_client: AsyncClient, door: str):  # noqa: F811
    kind = AMBIGUITY_DOORS[door][3]
    canonicals, value = await _ambiguous_value(kind)
    admin = {"X-API-Key": os.environ["API_KEY"]}
    probe = await file_client.get(f"{API}/{kind}s/{HIDDEN_NS}:{value}", headers=admin)
    assert probe.status_code == 409, probe.text  # the value really is ambiguous
    hidden_id = f"{HIDDEN_NS}:{value}"
    missing_id = f"{HIDDEN_NS}:NOPE_{uuid.uuid4().hex[:8].upper()}"

    hidden = await _ambiguity_call(file_client, door, hidden_id)
    missing = await _ambiguity_call(file_client, door, missing_id)

    for canonical in canonicals:  # no candidate is given away
        assert canonical not in hidden.text, hidden.text
    assert hidden.status_code == missing.status_code, (hidden.text, missing.text)
    assert hidden.text.replace(hidden_id, "<id>") == missing.text.replace(missing_id, "<id>")


# A field inside a document write names a hidden entity by a qualified id: the
# document's template, or a reference field's target. The write is refused
# either way; the refusal must read the same as for an id naming nothing.
REFERENCE_DOORS = {
    "create documents: template_id": ("template", "PERSON", {"template_id": "{id}", "data": {"first_name": "x"}}),
    "create documents: reference field": (
        "document", "CHAPTER_Q", {"template_id": "HOLDER_TWO_Q", "data": {"name": "x", "ref": "{id}"}},
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("door", list(REFERENCE_DOORS))
async def test_a_hidden_entity_named_inside_a_document_reads_as_an_unknown_one(
    file_client: AsyncClient, door: str,  # noqa: F811
):
    kind, template, body = REFERENCE_DOORS[door]
    canonical, value = await _hidden(kind, template)
    hidden_id = f"{HIDDEN_NS}:{value}"
    missing_id = f"{HIDDEN_NS}:NOPE_{uuid.uuid4().hex[:8].upper()}"

    async def call(entity_id: str):
        item = {"namespace": "wip", **{k: v for k, v in body.items() if k != "data"}}
        item["template_id"] = item["template_id"].format(id=entity_id)
        item["data"] = {k: (v.format(id=entity_id) if isinstance(v, str) else v) for k, v in body["data"].items()}
        return await file_client.post(f"{API}/documents", headers=APP_KEY, json=[item])

    hidden, missing = await call(hidden_id), await call(missing_id)

    assert missing.status_code == 200, missing.text  # the key is in effect
    assert missing.json()["results"][0]["status"] == "error", missing.text
    assert canonical not in hidden.text, hidden.text  # the canonical id is not given away
    assert hidden.text.replace(hidden_id, "<id>") == missing.text.replace(missing_id, "<id>")
