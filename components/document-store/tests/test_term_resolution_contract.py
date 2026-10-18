"""Document writes resolve term values exactly as def-store does.

These tests run document creation against the REAL def-store app mounted
in-process (and the real Registry), not the mock client the rest of the
suite uses: the contract is that a term field stores whatever def-store's
own resolver answers at write time. Each case is one way the old local copy
of the vocabulary disagreed with def-store — a deprecated term, a hard-
deleted term, an alias that moved, a case variant — and each must hold
immediately after the vocabulary changes, with no cache window.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from def_store.main import app as def_store_app
from def_store.models.audit_log import TermAuditLog
from def_store.models.term import Term
from def_store.models.term_relation import TermRelation
from def_store.models.terminology import Terminology
from def_store.services.registry_client import RegistryClient as DefStoreRegistryClient
from document_store.services.def_store_client import DefStoreClient, set_def_store_transport
from registry.main import app as registry_app
from tests.conftest import SAMPLE_TEMPLATES
from wip_auth import term_resolution

DOCS = "/api/document-store/documents"
AUTH = {"X-API-Key": os.environ["API_KEY"]}  # the suite's auth_headers


@pytest_asyncio.fixture
async def def_store(client: AsyncClient):
    """The real def-store, wired as document-store's term resolver.

    Yields an HTTP client for authoring the vocabulary in def-store; document
    writes through `client` then validate against that same def-store.
    """
    for model in (Term, Terminology, TermAuditLog, TermRelation):
        await model.delete_all()
    set_def_store_transport(ASGITransport(app=def_store_app))
    resolver = DefStoreClient(base_url="http://def-store", api_key=os.environ["API_KEY"])
    def_store_registry = DefStoreRegistryClient(
        base_url="http://registry", api_key=os.environ["MASTER_API_KEY"],
    )
    headers = {"X-API-Key": os.environ["API_KEY"]}
    try:
        with (
            patch("document_store.services.validation_service.get_def_store_client", return_value=resolver),
            patch("def_store.services.terminology_service.get_registry_client", return_value=def_store_registry),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=def_store_app), base_url="http://def-store", headers=headers,
            ) as ds:
                yield ds
    finally:
        set_def_store_transport(None)


@pytest_asyncio.fixture
async def people(def_store: AsyncClient, client: AsyncClient):
    """A mutable, case-insensitive vocabulary (ADA, LOVELACE, BABBAGE) and a
    template whose `who` field draws from it."""
    r = await def_store.post("/api/def-store/terminologies", json=[{
        "value": "CONTRACT_PEOPLE", "label": "People", "namespace": "wip",
        "mutable": True, "case_sensitive": False,
    }])
    tid = r.json()["results"][0]["id"]
    r = await def_store.post(f"/api/def-store/terminologies/{tid}/terms", json=[
        {"value": "ADA"}, {"value": "LOVELACE"}, {"value": "BABBAGE"},
    ])
    ids = {x["value"]: x["id"] for x in r.json()["results"]}

    headers = {"X-API-Key": os.environ["MASTER_API_KEY"]}
    async with AsyncClient(transport=ASGITransport(app=registry_app), base_url="http://registry") as reg:
        r = await reg.post("/api/registry/entries/register", headers=headers, json=[{
            "namespace": "wip", "entity_type": "templates",
            "composite_key": {"value": "CONTRACT_THING", "label": "Contract thing"},
        }])
        template_id = r.json()["results"][0]["registry_id"]
    SAMPLE_TEMPLATES[template_id] = {
        "template_id": template_id, "value": "CONTRACT_THING", "label": "Contract thing",
        "version": 1, "status": "active", "namespace": "wip",
        "identity_fields": ["name"], "header_fields": [], "rules": [],
        "usage": "entity", "source_templates": [], "target_templates": [], "versioned": True,
        "fields": [
            {"name": "name", "label": "Name", "type": "string", "mandatory": True},
            {"name": "who", "label": "Who", "type": "term", "terminology_ref": tid},
        ],
    }
    try:
        yield {"tid": tid, "ids": ids, "template_id": template_id}
    finally:
        SAMPLE_TEMPLATES.pop(template_id, None)


async def _write(client: AsyncClient, people: dict, name: str, who: str) -> dict:
    """Create one document; return its per-item result plus the stored term reference."""
    r = await client.post(DOCS, headers=AUTH, json=[{
        "namespace": "wip", "template_id": people["template_id"], "template_version": 1,
        "data": {"name": name, "who": who},
    }])
    body = r.json()
    assert "results" in body, (r.status_code, body)
    result = body["results"][0]
    if result["status"] == "error":
        return result
    doc = (await client.get(f"{DOCS}/{result['document_id']}", headers=AUTH)).json()
    (ref,) = [t for t in doc["term_references"] if t["field_path"] == "who"]
    return {**result, "term_id": ref["term_id"], "matched_via": ref["matched_via"]}


@pytest.mark.asyncio
async def test_value_and_case_variant_resolve(client, def_store, people):
    res = await _write(client, people, "d1", "lovelace")
    assert res["term_id"] == people["ids"]["LOVELACE"]
    assert res["matched_via"] == "value"


@pytest.mark.asyncio
async def test_hard_deleted_term_with_moved_alias_resolves_to_alias_owner_immediately(client, def_store, people):
    ids = people["ids"]
    # warm whatever might be warmed, then change the vocabulary
    assert (await _write(client, people, "warm", "LOVELACE"))["term_id"] == ids["LOVELACE"]
    r = await def_store.request("DELETE", "/api/def-store/terms", json=[{"id": ids["LOVELACE"]}])
    assert r.json()["results"][0]["status"] == "deleted"
    await def_store.put("/api/def-store/terms", json=[{"term_id": ids["ADA"], "aliases": ["LOVELACE"]}])

    for spelling in ("LOVELACE", "Lovelace"):
        res = await _write(client, people, f"after-{spelling}", spelling)
        assert res["term_id"] == ids["ADA"], (spelling, res)
        assert res["matched_via"] == "alias"


@pytest.mark.asyncio
async def test_deprecated_term_never_matches_a_new_write(client, def_store, people):
    ids = people["ids"]
    await def_store.post("/api/def-store/terms/deprecate", json=[{
        "term_id": ids["BABBAGE"], "reason": "merged", "replaced_by_term_id": ids["ADA"],
    }])
    res = await _write(client, people, "dep-no-alias", "BABBAGE")
    assert res["status"] == "error", res
    assert "is not valid for terminology" in res["error"], res

    await def_store.put("/api/def-store/terms", json=[{"term_id": ids["ADA"], "aliases": ["BABBAGE"]}])
    res = await _write(client, people, "dep-alias", "babbage")
    assert res["term_id"] == ids["ADA"]
    assert res["matched_via"] == "alias"


@pytest.mark.asyncio
async def test_removed_alias_stops_resolving_immediately(client, def_store, people):
    ids = people["ids"]
    await def_store.put("/api/def-store/terms", json=[{"term_id": ids["ADA"], "aliases": ["Countess"]}])
    assert (await _write(client, people, "with-alias", "Countess"))["term_id"] == ids["ADA"]
    await def_store.put("/api/def-store/terms", json=[{"term_id": ids["ADA"], "aliases": []}])
    res = await _write(client, people, "without-alias", "Countess")
    assert res["status"] == "error", res


@pytest.mark.asyncio
async def test_bulk_write_resolves_each_distinct_value_once(client, def_store, people):
    """A batch repeating values asks def-store once per distinct value."""
    calls = []
    real = term_resolution._resolve_remote

    async def counting(keys, *args):
        calls.append(list(keys))
        return await real(keys, *args)

    items = [{
        "namespace": "wip", "template_id": people["template_id"], "template_version": 1,
        "data": {"name": f"bulk-{i}", "who": ["ADA", "lovelace", "Ada"][i % 3]},
    } for i in range(12)]
    with patch.object(term_resolution, "_resolve_remote", counting):
        r = await client.post(DOCS, headers=AUTH, json=items)
    assert r.json()["succeeded"] == 12, r.json()
    asked = [value for batch in calls for _, value in batch]
    assert sorted(asked) == ["ADA", "Ada", "lovelace"]
