"""A refused reference is indistinguishable from a missing one.

'xns-closed' is closed to inbound references (the default), and only the
reader key can read it. For the writer key, a reference into it must produce
exactly what a reference to a target that does not exist produces: the same
error code, message, details, key order, and the same place among the
write's other errors. Otherwise a writer could test whether an id exists in
a namespace it cannot read. Each test compares the full result item of the
two cases, with the one differing id masked.
"""

import json

import pytest
from httpx import AsyncClient

API = "/api/document-store"
READER = {"X-API-Key": "test_xns_reader_key"}
WRITER = {"X-API-Key": "test_xns_writer_key"}
ABSENT_DOC = "0190c999-0000-7000-0000-00000000a999"
ABSENT_TEMPLATE = "0190c999-0000-7000-0000-00000000b999"
CLOSED_TEMPLATE = "0190c999-0000-7000-0000-00000000c999"


async def _post(client: AsyncClient, headers: dict, items: list[dict]) -> dict:
    resp = await client.post(f"{API}/documents", headers=headers, json=items)
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


def _masked(item: dict, *ids: str) -> str:
    """The item as raw JSON (key order included), with ids masked."""
    text = json.dumps(item)
    for value in ids:
        text = text.replace(value, "<ID>")
    return text


@pytest.fixture
async def ids(client: AsyncClient, auth_headers: dict) -> tuple[str, str]:
    """An experiment in 'wip' and a molecule in the closed namespace."""
    exp = await _post(client, auth_headers, [{
        "namespace": "wip", "template_id": "EXPERIMENT",
        "data": {"experiment_id": "EXP-P", "name": "x"},
    }])
    mol = await _post(client, auth_headers, [{
        "namespace": "xns-closed", "template_id": "MOLECULE",
        "data": {"molecule_id": "MOL-P", "name": "x"},
    }])
    assert exp["status"] == mol["status"] == "created", (exp, mol)
    return exp["document_id"], mol["document_id"]


def _edge(source: str, target: str) -> list[dict]:
    return [{
        "namespace": "wip", "template_id": "EXPERIMENT_INPUT",
        "data": {"source_ref": source, "target_ref": target, "role": "input"},
    }]


@pytest.mark.asyncio
async def test_a_refused_document_reference_reads_as_missing(client: AsyncClient, ids) -> None:
    exp_id, mol_id = ids
    refused = await _post(client, WRITER, _edge(exp_id, mol_id))
    missing = await _post(client, WRITER, _edge(exp_id, ABSENT_DOC))
    assert refused["status"] == "error"
    assert _masked(refused, mol_id) == _masked(missing, ABSENT_DOC)
    assert "xns-closed" not in json.dumps(refused)
    # The reader may reference it.
    assert (await _post(client, READER, _edge(exp_id, mol_id)))["status"] == "created"


@pytest.mark.asyncio
async def test_parity_holds_beside_another_invalid_reference(client: AsyncClient, ids) -> None:
    """Refusing after validation leaked here: with another field already
    invalid, only a missing target showed up among the errors."""
    _exp_id, mol_id = ids
    refused = await _post(client, WRITER, _edge(ABSENT_DOC, mol_id))
    missing = await _post(client, WRITER, _edge(ABSENT_DOC, ABSENT_DOC.replace("a999", "a998")))
    assert len(refused["details"]["errors"]) == 2
    assert _masked(refused, mol_id) == _masked(missing, ABSENT_DOC.replace("a999", "a998"))


@pytest.mark.asyncio
async def test_the_validate_endpoint_answers_the_same(client: AsyncClient, ids) -> None:
    exp_id, mol_id = ids

    async def check(target: str) -> dict:
        resp = await client.post(
            f"{API}/validation/validate", headers=WRITER,
            json={"namespace": "wip", "template_id": "EXPERIMENT_INPUT",
                  "data": {"source_ref": exp_id, "target_ref": target, "role": "input"}},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    refused, missing = await check(mol_id), await check(ABSENT_DOC)
    assert refused["valid"] is False
    assert _masked(refused, mol_id) == _masked(missing, ABSENT_DOC)


@pytest.fixture
def closed_template():
    from .conftest import SAMPLE_TEMPLATES

    SAMPLE_TEMPLATES[CLOSED_TEMPLATE] = {
        "template_id": CLOSED_TEMPLATE, "value": "XNS_PARITY", "label": "Parity",
        "version": 1, "status": "active", "namespace": "xns-closed",
        "identity_fields": ["code"], "header_fields": [],
        "fields": [{"name": "code", "type": "string", "label": "Code", "mandatory": True}],
        "rules": [], "usage": "entity", "source_templates": [], "target_templates": [],
        "versioned": True,
    }
    yield CLOSED_TEMPLATE
    SAMPLE_TEMPLATES.pop(CLOSED_TEMPLATE, None)


@pytest.mark.asyncio
async def test_a_template_in_a_closed_namespace_reads_as_missing(
    client: AsyncClient, closed_template: str,
) -> None:
    def item(template_id: str) -> list[dict]:
        return [{"namespace": "wip", "template_id": template_id,
                 "template_version": 1, "data": {"code": "X"}}]

    refused = await _post(client, WRITER, item(closed_template))
    missing = await _post(client, WRITER, item(ABSENT_TEMPLATE))
    assert refused["status"] == "error"
    assert _masked(refused, closed_template) == _masked(missing, ABSENT_TEMPLATE)
    assert (await _post(client, READER, item(closed_template)))["status"] == "created"


@pytest.mark.asyncio
async def test_a_patch_keeping_a_held_reference_is_not_refused(client: AsyncClient, ids) -> None:
    exp_id, mol_id = ids
    other = await _post(client, READER, [{
        "namespace": "wip", "template_id": "EXPERIMENT",
        "data": {"experiment_id": "EXP-Q", "name": "y"},
    }])
    edge = await _post(client, READER, _edge(exp_id, mol_id))
    assert edge["status"] == "created", edge

    async def patch(target: str) -> dict:
        resp = await client.patch(f"{API}/documents", headers=WRITER, json=[{
            "document_id": edge["document_id"], "patch": {"role": "changed"},
        }])
        assert resp.status_code == 200, resp.text
        return resp.json()["results"][0]

    # A PATCH that keeps the held reference is allowed for the writer.
    assert (await patch(mol_id))["status"] == "updated"
    assert other["status"] == "created"
