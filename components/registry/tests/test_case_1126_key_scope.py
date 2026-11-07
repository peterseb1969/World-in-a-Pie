"""A composite key that names a namespace is held only in that namespace, and
a key or value several entries hold is ambiguous, never answered with one.

Key claims are unique per (namespace, entity_type, hash), but the keys the
platform builds name their namespace ({"ns": ..., "type": "template",
"value": ...}). Before this, a writer of one namespace could add another
namespace's template key as a synonym on its own template, and /resolve,
which hashes the key without a namespace (wip-auth never sends one), then
answered with whichever of the two entries MongoDB returned first: a
reference in the victim's namespace resolved into the writer's.
/lookup/by-id's value fallback picked one entry the same way, and an
unreadable match could hide a readable one. Provenance: CASE-1126.
"""

import uuid

import pytest
from httpx import AsyncClient

from registry.models.entry import RegistryEntry, Synonym
from registry.services.hash import HashService

BASE = "/api/registry"


def _tag() -> str:
    return uuid.uuid4().hex[:8]


async def _post(client: AsyncClient, headers: dict, path: str, body) -> dict:
    r = await client.post(f"{BASE}{path}", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _mint_key(client: AsyncClient, auth_headers: dict, namespace: str, grant: str | None) -> dict:
    body: dict = {"name": f"case1126-{_tag()}", "groups": [], "namespaces": [namespace]}
    if grant:
        body["grant_permission"] = grant
    r = await client.post(f"{BASE}/api-keys", headers=auth_headers, json=body)
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["plaintext_key"]}


def _template_key(ns: str, value: str) -> dict:
    return {"ns": ns, "type": "template", "value": value}


async def _template(client: AsyncClient, auth_headers: dict, ns: str, value: str) -> str:
    data = await _post(client, auth_headers, "/entries/register", [{
        "namespace": ns, "entity_type": "templates", "composite_key": _template_key(ns, value),
    }])
    result = data["results"][0]
    assert result["status"] == "created", result
    return result["registry_id"]


async def _plant(entry_id: str, key: dict) -> None:
    """Store a synonym the way an install that predates the write-side
    check could hold it: under the entry's namespace, naming another."""
    entry = await RegistryEntry.find_one({"entry_id": entry_id})
    entry.synonyms.append(Synonym(
        namespace=entry.namespace, entity_type=entry.entity_type,
        composite_key=key, composite_key_hash=HashService.compute_composite_key_hash(key),
    ))
    entry.rebuild_search_values()
    await entry.save()


async def _resolve(client: AsyncClient, headers: dict, **item) -> dict:
    return (await _post(client, headers, "/entries/resolve", [item]))["results"][0]


# --- write side: a key's own 'ns' is its entry's namespace -----------------


@pytest.mark.asyncio
async def test_a_writer_cannot_plant_another_namespaces_key_as_a_synonym(
    client: AsyncClient, auth_headers: dict,
):
    tag = _tag()
    own = await _template(client, auth_headers, "vendor2", f"DECOY-{tag}")
    writer = await _mint_key(client, auth_headers, "vendor2", "write")
    data = await _post(client, writer, "/synonyms/add", [{
        "target_id": own, "synonym_composite_key": _template_key("vendor1", f"PATIENT-{tag}"),
    }])
    result = data["results"][0]
    assert result["status"] == "error", result
    assert result["details"]["rule"] == "synonym_scope_mismatch"
    # The victim's later create is untouched, and its key resolves only to it.
    victim = await _template(client, auth_headers, "vendor1", f"PATIENT-{tag}")
    found = await _resolve(
        client, auth_headers,
        entry_id=f"PATIENT-{tag}", composite_key=_template_key("vendor1", f"PATIENT-{tag}"),
        entity_type="templates",
    )
    assert (found["status"], found["entry_id"]) == ("found", victim)


@pytest.mark.asyncio
async def test_a_synonym_naming_its_own_namespace_is_added(client: AsyncClient, auth_headers: dict):
    tag = _tag()
    own = await _template(client, auth_headers, "vendor2", f"OWN-{tag}")
    data = await _post(client, auth_headers, "/synonyms/add", [{
        "target_id": own, "synonym_composite_key": _template_key("vendor2", f"ALIAS-{tag}"),
    }])
    assert data["results"][0]["status"] == "added", data


@pytest.mark.asyncio
async def test_register_refuses_a_key_naming_another_namespace(client: AsyncClient, auth_headers: dict):
    data = await _post(client, auth_headers, "/entries/register", [{
        "namespace": "vendor2", "entity_type": "templates",
        "composite_key": _template_key("vendor1", f"T-{_tag()}"),
    }])
    result = data["results"][0]
    assert result["status"] == "error", result
    assert result["details"]["rule"] == "key_scope_mismatch"


@pytest.mark.asyncio
async def test_a_system_qualified_key_may_carry_an_identity_field_named_ns(
    client: AsyncClient, auth_headers: dict,
):
    """A document's identity synonym holds its own fields, which may be
    called 'ns'; the '@template' member marks it as system-written."""
    tag = _tag()
    data = await _post(client, auth_headers, "/entries/register", [{
        "namespace": "vendor2", "entity_type": "documents",
        "composite_key": {"ns": "vendor2", "template_id": f"TPL-{tag}"},
        "identity_values": {"ns": "elsewhere"},
        "synonym_values": {"@template": f"T-{tag}", "ns": "elsewhere"},
    }])
    assert data["results"][0]["status"] == "created", data


@pytest.mark.asyncio
async def test_reserve_activate_and_provision_refuse_a_key_naming_another_namespace(
    client: AsyncClient, auth_headers: dict,
):
    foreign = _template_key("vendor1", f"T-{_tag()}")

    reserved = await _post(client, auth_headers, "/entries/reserve", [{
        "entry_id": str(uuid.uuid4()), "namespace": "vendor2",
        "entity_type": "templates", "composite_key": foreign,
    }])
    assert reserved["results"][0]["details"]["rule"] == "key_scope_mismatch", reserved

    r = await client.post(f"{BASE}/entries/provision", headers=auth_headers, json={
        "namespace": "vendor2", "entity_type": "templates", "composite_keys": [foreign],
    })
    assert r.status_code == 422, r.text

    r = await client.post(f"{BASE}/entries/provision", headers=auth_headers, json={
        "namespace": "vendor2", "entity_type": "templates", "defer_claim": True,
    })
    assert r.status_code == 200, r.text
    entry_id = r.json()["ids"][0]["entry_id"]
    activated = await _post(client, auth_headers, "/entries/activate", [{
        "entry_id": entry_id, "composite_key": foreign,
    }])
    assert activated["results"][0]["details"]["rule"] == "key_scope_mismatch", activated

    activated = await _post(client, auth_headers, "/entries/activate", [{
        "entry_id": entry_id, "composite_key": _template_key("vendor2", f"T-{_tag()}"),
        "synonyms": [{"synonym_composite_key": foreign}],
    }])
    assert activated["results"][0]["details"]["rule"] == "synonym_scope_mismatch", activated


# --- read side: /resolve -----------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_scopes_a_key_to_the_namespace_it_names(client: AsyncClient, auth_headers: dict):
    """A key planted before the write-side check (older entry, so the first
    one the database returns) no longer answers for the namespace it names."""
    tag = _tag()
    key = _template_key("vendor1", f"PATIENT-{tag}")
    decoy = await _template(client, auth_headers, "vendor2", f"DECOY-{tag}")
    await _plant(decoy, key)
    victim = await _template(client, auth_headers, "vendor1", f"PATIENT-{tag}")
    # The payload wip-auth builds for a bare value: no namespace.
    found = await _resolve(
        client, auth_headers,
        entry_id=f"PATIENT-{tag}", composite_key=key, entity_type="templates",
    )
    assert (found["status"], found["entry_id"]) == ("found", victim)


@pytest.mark.asyncio
async def test_resolve_refuses_a_key_two_readable_entries_hold(client: AsyncClient, auth_headers: dict):
    tag = _tag()
    key = {"@template": f"T-{tag}", "title": "same"}
    first = await _template(client, auth_headers, "vendor1", f"A-{tag}")
    second = await _template(client, auth_headers, "vendor2", f"B-{tag}")
    await _plant(first, key)
    await _plant(second, key)
    result = await _resolve(client, auth_headers, composite_key=key, entity_type="templates")
    assert result["status"] == "ambiguous", result
    assert result["entry_id"] is None
    assert [c["entry_id"] for c in result["candidates"]] == [first, second]
    assert {c["namespace"] for c in result["candidates"]} == {"vendor1", "vendor2"}
    # Naming the namespace picks out one.
    scoped = await _resolve(
        client, auth_headers, composite_key=key, entity_type="templates", namespace="vendor2",
    )
    assert (scoped["status"], scoped["entry_id"]) == ("found", second)


@pytest.mark.asyncio
async def test_resolve_ignores_an_entry_the_caller_cannot_read(client: AsyncClient, auth_headers: dict):
    """An unreadable holder neither wins nor hides the readable one."""
    tag = _tag()
    key = {"@template": f"T-{tag}", "title": "same"}
    hidden = await _template(client, auth_headers, "vendor2", f"A-{tag}")
    readable = await _template(client, auth_headers, "vendor1", f"B-{tag}")
    await _plant(hidden, key)
    await _plant(readable, key)
    reader = await _mint_key(client, auth_headers, "vendor1", "read")
    result = await _resolve(client, reader, composite_key=key, entity_type="templates")
    assert (result["status"], result["entry_id"]) == ("found", readable)


@pytest.mark.asyncio
async def test_resolve_counts_ambiguous_items(client: AsyncClient, auth_headers: dict):
    tag = _tag()
    key = {"@template": f"T-{tag}", "title": "same"}
    for ns in ("vendor1", "vendor2"):
        await _plant(await _template(client, auth_headers, ns, f"{ns}-{tag}"), key)
    data = await _post(client, auth_headers, "/entries/resolve", [
        {"composite_key": key, "entity_type": "templates"},
        {"composite_key": {"@template": "none", "title": tag}, "entity_type": "templates"},
    ])
    assert (data["found"], data["not_found"], data["ambiguous"], data["errors"]) == (0, 1, 1, 0)


# --- read side: /lookup/by-id ------------------------------------------------


async def _lookup(client: AsyncClient, headers: dict, **item) -> dict:
    return (await _post(client, headers, "/entries/lookup/by-id", [item]))["results"][0]


@pytest.mark.asyncio
async def test_lookup_by_id_refuses_a_value_two_readable_entries_carry(
    client: AsyncClient, auth_headers: dict,
):
    tag = _tag()
    first = await _template(client, auth_headers, "vendor1", f"SHARED-{tag}")
    second = await _template(client, auth_headers, "vendor2", f"SHARED-{tag}")
    result = await _lookup(client, auth_headers, entry_id=f"SHARED-{tag}")
    assert result["status"] == "ambiguous", result
    assert [c["entry_id"] for c in result["candidates"]] == [first, second]
    scoped = await _lookup(client, auth_headers, entry_id=f"SHARED-{tag}", namespace="vendor2")
    assert (scoped["status"], scoped["entry_id"]) == ("found", second)


@pytest.mark.asyncio
async def test_lookup_by_id_ignores_an_entry_the_caller_cannot_read(
    client: AsyncClient, auth_headers: dict,
):
    tag = _tag()
    await _template(client, auth_headers, "vendor2", f"SHARED-{tag}")
    readable = await _template(client, auth_headers, "vendor1", f"SHARED-{tag}")
    reader = await _mint_key(client, auth_headers, "vendor1", "read")
    result = await _lookup(client, reader, entry_id=f"SHARED-{tag}")
    assert (result["status"], result["entry_id"]) == ("found", readable)
