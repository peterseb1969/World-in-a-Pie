"""The Registry's entry, synonym and search routes enforce namespace
authorization. Provenance: CASE-910.

Before this, every one of these routes accepted any valid API key and
never looked at the caller's namespaces: a key scoped to one namespace
could read another namespace's entries, attach synonyms to them, squat a
composite key inside it (which that namespace's own later registration
then silently adopted), and register entries there directly.

The contract pinned here, with real runtime keys through the real
provider chain:
- the identity-allocation routes (register, provision, reserve, activate,
  entry update and delete) answer 403 to any non-privileged key, even in
  its own namespace — they are the stores' internal protocol;
- synonym add/remove and merge need write on every namespace they touch;
- reads (get, lookups, resolve, search) see only namespaces the caller can
  read, and report everything else as not found;
- the master key keeps full access.
"""

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

from registry.api.grants import _resolve_permission
from wip_auth import UserIdentity

BASE = "/api/registry"


async def _mint_key(
    client: AsyncClient, auth_headers: dict, namespace: str, grant: str | None
) -> dict:
    body: dict = {
        "name": f"case910-{uuid.uuid4().hex[:8]}",
        "groups": [],
        "namespaces": [namespace],
    }
    if grant:
        body["grant_permission"] = grant
    resp = await client.post(f"{BASE}/api-keys", headers=auth_headers, json=body)
    assert resp.status_code == 201, resp.text
    return {"X-API-Key": resp.json()["plaintext_key"]}


async def _register(client: AsyncClient, auth_headers: dict, namespace: str, value: str) -> str:
    resp = await client.post(
        f"{BASE}/entries/register",
        headers=auth_headers,
        json=[{
            "namespace": namespace,
            "entity_type": "documents",
            "composite_key": {"value": value},
        }],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]["registry_id"]


@pytest_asyncio.fixture
async def setup(client: AsyncClient, auth_headers: dict) -> dict:
    """vendor1 is the caller's namespace, vendor2 a foreign one."""
    tag = uuid.uuid4().hex[:8]
    return {
        "tag": tag,
        "own": await _register(client, auth_headers, "vendor1", f"own-{tag}"),
        "own2": await _register(client, auth_headers, "vendor1", f"own2-{tag}"),
        "foreign": await _register(client, auth_headers, "vendor2", f"foreign-{tag}"),
        "writer": await _mint_key(client, auth_headers, "vendor1", "write"),
        "reader": await _mint_key(client, auth_headers, "vendor1", None),
    }


# Every allocation route, with a body that fails schema validation: the
# privileged gate is a dependency, so it answers before validation runs —
# a non-privileged key gets 403 and nothing can be written even if the gate
# were open (it would answer 422 instead).
ALLOCATION_ROUTES = [
    ("POST", f"{BASE}/entries/register"),
    ("POST", f"{BASE}/entries/provision"),
    ("POST", f"{BASE}/entries/reserve"),
    ("POST", f"{BASE}/entries/activate"),
    ("PUT", f"{BASE}/entries"),
    ("DELETE", f"{BASE}/entries"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", ALLOCATION_ROUTES)
async def test_allocation_refuses_a_namespace_writer(
    client: AsyncClient, setup: dict, method: str, path: str
):
    resp = await client.request(method, path, headers=setup["writer"], json={"bogus": 1})
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", ALLOCATION_ROUTES)
async def test_allocation_admits_the_master_key(
    client: AsyncClient, auth_headers: dict, method: str, path: str
):
    resp = await client.request(method, path, headers=auth_headers, json={"bogus": 1})
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_register_in_own_namespace_is_refused(client: AsyncClient, setup: dict):
    resp = await client.post(
        f"{BASE}/entries/register",
        headers=setup["writer"],
        json=[{"namespace": "vendor1", "entity_type": "documents",
               "composite_key": {"value": f"direct-{setup['tag']}"}}],
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_synonym_on_own_entry_is_added(client: AsyncClient, setup: dict):
    resp = await client.post(
        f"{BASE}/synonyms/add",
        headers=setup["writer"],
        json=[{"target_id": setup["own"], "synonym_namespace": "vendor1",
               "synonym_entity_type": "documents",
               "synonym_composite_key": {"erp_id": f"ok-{setup['tag']}"}}],
    )
    assert resp.json()["results"][0]["status"] == "added"


@pytest.mark.asyncio
async def test_synonym_on_foreign_entry_is_not_found(client: AsyncClient, setup: dict):
    resp = await client.post(
        f"{BASE}/synonyms/add",
        headers=setup["writer"],
        json=[{"target_id": setup["foreign"], "synonym_namespace": "vendor2",
               "synonym_entity_type": "documents",
               "synonym_composite_key": {"erp_id": f"x-{setup['tag']}"}}],
    )
    assert resp.json()["results"][0]["status"] == "target_not_found"


@pytest.mark.asyncio
async def test_squatting_a_foreign_namespace_is_forbidden(
    client: AsyncClient, auth_headers: dict, setup: dict
):
    """The hijack from CASE-908#1: a synonym on the caller's own entry, but
    claimed inside another namespace."""
    squat = {"value": f"squatted-{setup['tag']}"}
    resp = await client.post(
        f"{BASE}/synonyms/add",
        headers=setup["writer"],
        json=[{"target_id": setup["own"], "synonym_namespace": "vendor2",
               "synonym_entity_type": "documents", "synonym_composite_key": squat}],
    )
    result = resp.json()["results"][0]
    assert result["status"] == "forbidden"
    assert "vendor2" in result["error"]

    # vendor2's own registration of that value gets a fresh identity.
    registered = await client.post(
        f"{BASE}/entries/register",
        headers=auth_headers,
        json=[{"namespace": "vendor2", "entity_type": "documents", "composite_key": squat}],
    )
    assert registered.json()["results"][0]["status"] == "created"


@pytest.mark.asyncio
async def test_read_only_key_cannot_add_synonyms(client: AsyncClient, setup: dict):
    resp = await client.post(
        f"{BASE}/synonyms/add",
        headers=setup["reader"],
        json=[{"target_id": setup["own"], "synonym_namespace": "vendor1",
               "synonym_entity_type": "documents",
               "synonym_composite_key": {"erp_id": f"ro-{setup['tag']}"}}],
    )
    assert resp.json()["results"][0]["status"] == "forbidden"


@pytest.mark.asyncio
async def test_synonym_remove_on_foreign_entry_is_not_found(
    client: AsyncClient, auth_headers: dict, setup: dict
):
    synonym = {"target_id": setup["foreign"], "synonym_namespace": "vendor2",
               "synonym_entity_type": "documents",
               "synonym_composite_key": {"erp_id": f"keep-{setup['tag']}"}}
    added = await client.post(f"{BASE}/synonyms/add", headers=auth_headers, json=[synonym])
    assert added.json()["results"][0]["status"] == "added"

    resp = await client.post(f"{BASE}/synonyms/remove", headers=setup["writer"], json=[synonym])
    assert resp.json()["results"][0]["status"] == "not_found"

    entry = await client.get(f"{BASE}/entries/{setup['foreign']}", headers=auth_headers)
    assert any(s["composite_key"] == synonym["synonym_composite_key"]
               for s in entry.json()["synonyms"])


@pytest.mark.asyncio
async def test_merge_across_into_a_foreign_namespace_is_refused(client: AsyncClient, setup: dict):
    resp = await client.post(
        f"{BASE}/synonyms/merge",
        headers=setup["writer"],
        json=[{"preferred_id": setup["own"], "deprecated_id": setup["foreign"]}],
    )
    assert resp.json()["results"][0]["status"] == "deprecated_not_found"


@pytest.mark.asyncio
async def test_merge_within_own_namespace_is_allowed(client: AsyncClient, setup: dict):
    resp = await client.post(
        f"{BASE}/synonyms/merge",
        headers=setup["writer"],
        json=[{"preferred_id": setup["own"], "deprecated_id": setup["own2"]}],
    )
    assert resp.json()["results"][0]["status"] == "merged"


@pytest.mark.asyncio
async def test_merge_needs_write(client: AsyncClient, setup: dict):
    resp = await client.post(
        f"{BASE}/synonyms/merge",
        headers=setup["reader"],
        json=[{"preferred_id": setup["own"], "deprecated_id": setup["own2"]}],
    )
    assert resp.json()["results"][0]["status"] == "forbidden"


@pytest.mark.asyncio
async def test_get_foreign_entry_is_404(client: AsyncClient, auth_headers: dict, setup: dict):
    assert (await client.get(f"{BASE}/entries/{setup['foreign']}", headers=setup["reader"])).status_code == 404
    assert (await client.get(f"{BASE}/entries/{setup['own']}", headers=setup["reader"])).status_code == 200
    assert (await client.get(f"{BASE}/entries/{setup['foreign']}", headers=auth_headers)).status_code == 200


@pytest.mark.asyncio
async def test_lookups_hide_foreign_entries(client: AsyncClient, auth_headers: dict, setup: dict):
    tag = setup["tag"]
    by_key = [
        {"namespace": "vendor2", "entity_type": "documents", "composite_key": {"value": f"foreign-{tag}"}},
        {"namespace": "vendor1", "entity_type": "documents", "composite_key": {"value": f"own-{tag}"}},
    ]
    by_id = [{"entry_id": setup["foreign"]}, {"entry_id": setup["own"]}]
    resolve = [{"entry_id": setup["foreign"]}, {"entry_id": setup["own"]}]

    for path, body in (("lookup/by-key", by_key), ("lookup/by-id", by_id), ("resolve", resolve)):
        scoped = await client.post(f"{BASE}/entries/{path}", headers=setup["reader"], json=body)
        assert [r["status"] for r in scoped.json()["results"]] == ["not_found", "found"], path
        master = await client.post(f"{BASE}/entries/{path}", headers=auth_headers, json=body)
        assert [r["status"] for r in master.json()["results"]] == ["found", "found"], path


@pytest.mark.asyncio
async def test_search_sees_only_readable_namespaces(
    client: AsyncClient, auth_headers: dict, setup: dict
):
    tag = setup["tag"]
    by_term = [{"term": tag}]
    by_fields = [{"field_criteria": {"value": f"foreign-{tag}"}}]

    scoped = await client.post(f"{BASE}/search/by-term", headers=setup["reader"], json=by_term)
    namespaces = {r["namespace"] for r in scoped.json()["results"][0]["results"]}
    assert namespaces == {"vendor1"}
    assert scoped.json()["results"][0]["total_matches"] == 2

    for path in ("search/by-fields", "search/across-namespaces"):
        scoped = await client.post(f"{BASE}/{path}", headers=setup["reader"], json=by_fields)
        assert scoped.json()["results"][0]["results"] == [], path
        master = await client.post(f"{BASE}/{path}", headers=auth_headers, json=by_fields)
        assert len(master.json()["results"][0]["results"]) == 1, path


@pytest.mark.asyncio
async def test_config_declared_grants_resolve_locally(client: AsyncClient):
    """A spec-declared key carries its grants in its own definition; the
    Registry must answer what wip-auth answers everywhere else."""
    identity = UserIdentity(
        user_id="apikey:spec-key",
        username="spec-key",
        groups=["wip-users"],
        auth_method="api_key",
        raw_claims={"namespaces": ["vendor1", "vendor2"], "grants": {"vendor1": "write"}},
    )
    assert await _resolve_permission(identity, "vendor1") == "write"
    assert await _resolve_permission(identity, "vendor2") == "read"
    assert await _resolve_permission(identity, "default") == "none"
