"""A prefixed ID sequence never mints an ID that already exists.

The counter was kept per (namespace, entity type, prefix) and moved only by
its own increments. An ID-preserving restore re-registers its IDs without
touching it, so the first new ID after a restore was the first restored one
(E11000 on entry_id). A prefix that moved to another namespace or entity type
started a new counter at 1, colliding with the IDs minted before. A
caller-supplied ID in the prefix's range was minted again later. Now the
counter is per prefix, install-wide; an entry written with its own prefixed
ID raises it; the first mint from a prefix starts above every ID the prefix
already has; and a prefixed config without a prefix refuses to mint.
Provenance: CASE-1125.
"""

import uuid

import pytest
from httpx import AsyncClient
from pymongo.errors import BulkWriteError

from registry.models.entry import RegistryEntry
from registry.models.id_counter import IdCounter
from registry.models.namespace import Namespace

BASE = "/api/registry"


def _prefix() -> str:
    return f"P{uuid.uuid4().hex[:6].upper()}-"


async def _namespace(client: AsyncClient, headers: dict, name: str, **prefixes: str) -> None:
    r = await client.put(f"{BASE}/namespaces/{name}", headers=headers, json={
        "id_config": {t: {"algorithm": "prefixed", "prefix": p, "pad": 6} for t, p in prefixes.items()},
    })
    assert r.status_code == 200, r.text


async def _register(client: AsyncClient, headers: dict, ns: str, *items: dict, entity_type: str = "terminologies") -> list[dict]:
    r = await client.post(f"{BASE}/entries/register", headers=headers, json=[
        {"namespace": ns, "entity_type": entity_type, "composite_key": {"ns": ns, "value": uuid.uuid4().hex}, **item}
        for item in items
    ])
    assert r.status_code == 200, r.text
    return r.json()["results"]


async def _mint(client: AsyncClient, headers: dict, ns: str, entity_type: str = "terminologies") -> str:
    (result,) = await _register(client, headers, ns, {}, entity_type=entity_type)
    assert result["status"] == "created", result
    return result["registry_id"]


def _ns() -> str:
    return f"c1125-{uuid.uuid4().hex[:6]}"


@pytest.mark.asyncio
async def test_a_restore_shaped_write_then_a_mint(client: AsyncClient, auth_headers: dict):
    """IDs re-registered with their numbers (a restore) are never minted again."""
    ns, p = _ns(), _prefix()
    await _namespace(client, auth_headers, ns, terminologies=p)
    restored = await _register(client, auth_headers, ns, *({"entry_id": f"{p}{n:06d}"} for n in (1, 2, 3)))
    assert [r["status"] for r in restored] == ["created"] * 3
    assert await _mint(client, auth_headers, ns) == f"{p}000004"


@pytest.mark.asyncio
async def test_a_caller_id_in_one_batch_is_not_minted_in_the_same_batch(
    client: AsyncClient, auth_headers: dict,
):
    ns, p = _ns(), _prefix()
    await _namespace(client, auth_headers, ns, terminologies=p)
    results = await _register(client, auth_headers, ns, {"entry_id": f"{p}000001"}, {})
    assert [(r["status"], r["registry_id"]) for r in results] == [
        ("created", f"{p}000001"), ("created", f"{p}000002"),
    ]


@pytest.mark.asyncio
async def test_a_caller_id_ahead_of_the_counter_moves_it(client: AsyncClient, auth_headers: dict):
    ns, p = _ns(), _prefix()
    await _namespace(client, auth_headers, ns, terminologies=p)
    assert await _mint(client, auth_headers, ns) == f"{p}000001"
    await _register(client, auth_headers, ns, {"entry_id": f"{p}000010"})
    assert await _mint(client, auth_headers, ns) == f"{p}000011"


@pytest.mark.asyncio
async def test_a_reserved_caller_id_moves_the_counter(client: AsyncClient, auth_headers: dict):
    ns, p = _ns(), _prefix()
    await _namespace(client, auth_headers, ns, terminologies=p)
    r = await client.post(f"{BASE}/entries/reserve", headers=auth_headers, json=[
        {"entry_id": f"{p}000007", "namespace": ns, "entity_type": "terminologies"},
    ])
    assert r.json()["results"][0]["status"] == "reserved", r.text
    assert await _mint(client, auth_headers, ns) == f"{p}000008"


@pytest.mark.asyncio
async def test_a_prefix_moved_to_another_namespace_continues(client: AsyncClient, auth_headers: dict):
    a, b, p = _ns(), _ns(), _prefix()
    await _namespace(client, auth_headers, a, terminologies=p)
    assert await _mint(client, auth_headers, a) == f"{p}000001"
    await _namespace(client, auth_headers, a, terminologies=_prefix())
    await _namespace(client, auth_headers, b, terminologies=p)
    assert await _mint(client, auth_headers, b) == f"{p}000002"


@pytest.mark.asyncio
async def test_a_prefix_moved_to_another_entity_type_continues(client: AsyncClient, auth_headers: dict):
    ns, p = _ns(), _prefix()
    await _namespace(client, auth_headers, ns, terminologies=p)
    assert await _mint(client, auth_headers, ns) == f"{p}000001"
    await _namespace(client, auth_headers, ns, terminologies=_prefix(), terms=p)
    assert await _mint(client, auth_headers, ns, entity_type="terms") == f"{p}000002"


@pytest.mark.asyncio
async def test_the_first_mint_starts_above_existing_entries(client: AsyncClient, auth_headers: dict):
    """Entries stored with no counter behind them (an install whose counter
    fell behind before this rule), including one past its pad width."""
    ns, p = _ns(), _prefix()
    await _namespace(client, auth_headers, ns, terminologies=p)
    for entry_id in (f"{p}000009", f"{p}1000000", f"{p}000123"):
        await RegistryEntry(
            entry_id=entry_id, namespace=ns, entity_type="terminologies",
            primary_composite_key={}, primary_composite_key_hash="", status="active",
        ).insert()
    assert await _mint(client, auth_headers, ns) == f"{p}1000001"


@pytest.mark.asyncio
async def test_the_first_mint_starts_above_an_older_counter(client: AsyncClient, auth_headers: dict):
    """A per-namespace counter of the older form, for another namespace that
    held the prefix: its numbers were handed out."""
    ns, p = _ns(), _prefix()
    await IdCounter(counter_key=f"gone-ns:terminologies:{p}", seq=41).insert()
    await _namespace(client, auth_headers, ns, terminologies=p)
    assert await _mint(client, auth_headers, ns) == f"{p}000042"


@pytest.mark.asyncio
async def test_a_prefixed_config_without_a_prefix_refuses_to_mint(
    client: AsyncClient, auth_headers: dict,
):
    """Stored before writes refused it: it would mint bare numbers."""
    ns = _ns()
    await Namespace(prefix=ns, id_config={
        "terminologies": {"algorithm": "prefixed", "prefix": "", "pad": 6},
    }).insert()
    (result,) = await _register(client, auth_headers, ns, {})
    assert result["status"] == "error", result
    assert result["registry_id"] is None
    r = await client.post(f"{BASE}/entries/provision", headers=auth_headers, json={
        "namespace": ns, "entity_type": "terminologies",
    })
    assert r.status_code == 422, r.text


def test_a_taken_entry_id_is_named_without_the_database_error():
    from registry.api.entries import _insert_failure

    e = BulkWriteError({"writeErrors": [{
        "index": 0, "code": 11000,
        "errmsg": "E11000 duplicate key error collection: wip_registry.registry_entries "
                  "index: entry_id_unique_idx dup key: { entry_id: \"P-000001\" }",
        "keyValue": {"entry_id": "P-000001"},
        "op": {"primary_composite_key_hash": "abc123", "primary_composite_key": {"value": "secret"}},
    }]})
    fields = _insert_failure(e)
    assert fields["error_code"] == "already_exists"
    assert fields["details"] == {"rule": "entry_id_taken", "entry_id": "P-000001"}
    assert "abc123" not in fields["error"] and "secret" not in fields["error"]
    other = _insert_failure(RuntimeError("boom with abc123"))
    assert other["error_code"] == "internal_error" and "abc123" not in other["error"]
