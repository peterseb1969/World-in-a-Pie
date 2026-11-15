"""A Registry entry whose records are gone answers exactly as an id that names
nothing — same status, same body.

The id-keyed document and file doors resolved the id, read nothing, and answered
their own miss ("Document not found", "File not found") while an id nobody ever
issued was refused by resolve_or_404 with "Could not resolve …". The difference
therefore still said whether an id exists anywhere on the platform, which is what
CASE-1199 set out to make unanswerable. A ghost like this is what a hard delete
leaves when the Registry refuses to release the entry, and what
/maintenance/registry-ghosts reports.

Two keys, because their miss differs: the store's own key is scoped to 'wip'
alone, so ids resolve there and an unknown one is the resolving 404; a key
scoped to several namespaces has none to resolve in, so ids pass through
unresolved and the door's own miss is the answer.

Each ghost id is checked against the Registry before the door is called: an id
that does not resolve would answer the same way for the wrong reason.

Provenance: CASE-1300, split from CASE-1264. def-store and template-store carry
the same rule in their test_hidden_answers_as_missing beds.
"""

import uuid

import pytest
from httpx import AsyncClient

from document_store.models.document import Document
from document_store.models.file import File
from tests.conftest import SAMPLE_TEMPLATES
from tests.test_files import _insert_file, file_client  # noqa: F401 — fixtures
from wip_auth.resolve import EntityNotFoundError, resolve_entity_id

DOCS = "/api/document-store/documents"
FILES = "/api/document-store/files"
# Scoped to 'wip' and 'xns-closed': no namespace to resolve an id in.
MULTI_NS = {"X-API-Key": "test_xns_reader_key"}
MODES = ["resolving", "pass-through"]

DOC_DOORS = [
    ("GET", "{id}"),
    ("GET", "{id}/versions"),
    ("GET", "{id}/versions/1"),
    ("GET", "{id}/latest"),
    ("GET", "{id}/relationships"),
    ("GET", "{id}/traverse"),
]
FILE_DOORS = [
    ("GET", "{id}"),
    ("GET", "{id}/download"),
    ("GET", "{id}/content"),
    ("GET", "{id}/documents"),
    ("DELETE", "{id}/hard"),
]


async def _resolves(entity_id: str, entity_type: str) -> bool:
    """Whether the Registry answers for this id in 'wip', as the doors ask it."""
    try:
        await resolve_entity_id(entity_id, entity_type, "wip")
    except EntityNotFoundError:
        return False
    return True


def _headers(mode: str, auth_headers: dict) -> dict:
    return auth_headers if mode == "resolving" else MULTI_NS


async def _assert_answers_as_missing(
    client: AsyncClient, headers: dict, base: str, method: str, path: str,
    ghost_id: str, entity_type: str, resolving: bool,
) -> None:
    assert await _resolves(ghost_id, entity_type), f"{ghost_id} must still resolve"
    missing_id = str(uuid.uuid4())
    assert not await _resolves(missing_id, entity_type), "the comparison id must not resolve"

    ghost = await client.request(method, f"{base}/{path.format(id=ghost_id)}", headers=headers)
    missing = await client.request(method, f"{base}/{path.format(id=missing_id)}", headers=headers)
    assert missing.status_code not in (401, 403), missing.text  # the key is in effect
    if resolving:
        assert "Could not resolve" in missing.text, missing.text  # the resolving path
    assert ghost.status_code == missing.status_code, (ghost.text, missing.text)
    assert ghost.text.replace(ghost_id, "<id>") == missing.text.replace(missing_id, "<id>")


async def _ghost_document(client: AsyncClient, headers: dict) -> str:
    """A stored document with every row of it removed, its entry left behind."""
    national_id = f"{uuid.uuid4().int % 10**9:09d}"  # the template wants nine digits
    response = await client.post(DOCS, headers=headers, json=[{
        "namespace": "wip",
        "template_id": SAMPLE_TEMPLATES["PERSON"]["template_id"],
        "data": {
            "national_id": national_id, "first_name": "Ghost",
            "last_name": "Entry", "birth_date": "1990-01-15", "gender": "M", "age": 34,
        },
    }])
    result = response.json()["results"][0]
    assert result["status"] in ("created", "updated"), result
    document_id = result["id"]
    await Document.find({"document_id": document_id}).delete()
    return document_id


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("method,path", DOC_DOORS)
async def test_a_document_entry_without_its_rows_answers_as_a_missing_one(
    client: AsyncClient, auth_headers: dict, mode: str, method: str, path: str,
):
    headers = _headers(mode, auth_headers)
    ghost_id = await _ghost_document(client, auth_headers)
    await _assert_answers_as_missing(
        client, headers, DOCS, method, path, ghost_id, "document", mode == "resolving",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("method,path", FILE_DOORS)
async def test_a_file_entry_without_its_record_answers_as_a_missing_one(
    file_client: AsyncClient,  # noqa: F811 — the fixture imported for pytest to find
    auth_headers: dict, mode: str, method: str, path: str,
):
    ghost = await _insert_file(namespace="wip")
    await File.find({"file_id": ghost.file_id}).delete()
    await _assert_answers_as_missing(
        file_client, _headers(mode, auth_headers), FILES, method, path,
        ghost.file_id, "file", mode == "resolving",
    )
