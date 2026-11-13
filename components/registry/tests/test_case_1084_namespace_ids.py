"""Every namespace has an id of its own, apart from its name.

A namespace was known only by its name, so "the namespace this grant was made
for" and "a later namespace that happens to have the same name" could not be
told apart: a system restore matched orphaned grants to a namespace by name
and reservation owner, and an unreserved name carried no identity at all.
Now the Registry mints a namespace_id (UUID7): a reservation holds one and the
namespace created under it takes it; an ID-preserving restore or import
re-creates the archived one; deleting a namespace ends it. Orphaned grants
carry the id they were made for and are activated by a namespace created with
it, whoever creates it. Archives without namespace ids are refused.
Provenance: CASE-1084.
"""

import json
import uuid

import pytest

from registry.models.grant import NamespaceGrant
from registry.models.namespace import Namespace
from registry.models.reservation import RuntimeReservation

KEYS = "/api/registry/api-keys"
NS = "/api/registry/namespaces"
BACKUP = "/api/registry/system-backup"
RESTORE = "/api/registry/system-restore"


def _p(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


def _uuid() -> str:
    return str(uuid.uuid4())


async def _key(client, headers, name, namespaces, **extra) -> dict:
    resp = await client.post(KEYS, headers=headers, json={"name": name, "namespaces": namespaces, **extra})
    assert resp.status_code == 201, resp.text
    return {"X-API-Key": resp.json()["plaintext_key"]}


async def _upsert(client, headers, prefix, **body):
    return await client.put(f"{NS}/{prefix}", json=body, headers=headers)


async def _reserved_id(prefix: str) -> str | None:
    r = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
    return r.namespace_id if r is not None else None


async def _orphan(prefix: str, subject: str, namespace_id: str | None) -> None:
    await NamespaceGrant(
        namespace=prefix, subject=subject, subject_type="group", permission="read",
        granted_by="restore", orphaned=True, namespace_id=namespace_id,
    ).insert()


async def _is_orphan(prefix: str, subject: str) -> bool:
    g = await NamespaceGrant.find_one({"namespace": prefix, "subject": subject})
    return g.orphaned


# --- minting ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_created_namespace_has_its_own_id(client, auth_headers):
    a, b = _p("a"), _p("b")
    ra = await _upsert(client, auth_headers, a)
    rb = await client.post(NS, headers=auth_headers, json={"prefix": b})
    ids = {ra.json()["namespace_id"], rb.json()["namespace_id"]}
    assert len(ids) == 2 and None not in ids
    assert (await client.get(f"{NS}/{a}", headers=auth_headers)).json()["namespace_id"] == ra.json()["namespace_id"]


@pytest.mark.asyncio
async def test_a_namespace_created_under_a_reservation_takes_its_id(client, auth_headers):
    prefix, owner = _p("app"), _p("owner")
    owner_key = await _key(client, auth_headers, owner, [prefix], reserve=[{"prefix": prefix}])
    reserved = await _reserved_id(prefix)
    assert reserved
    created = await _upsert(client, owner_key, prefix)
    assert created.status_code == 200, created.text
    assert created.json()["namespace_id"] == reserved


@pytest.mark.asyncio
async def test_a_reservation_of_an_existing_namespace_takes_its_id(client, auth_headers):
    prefix = _p("here")
    ns_id = (await _upsert(client, auth_headers, prefix)).json()["namespace_id"]
    await _key(client, auth_headers, _p("owner"), [prefix], reserve=[{"prefix": prefix}])
    assert await _reserved_id(prefix) == ns_id


@pytest.mark.asyncio
async def test_an_update_never_changes_the_id(client, auth_headers):
    prefix = _p("keep")
    ns_id = (await _upsert(client, auth_headers, prefix)).json()["namespace_id"]
    again = await _upsert(client, auth_headers, prefix, description="x", namespace_id=_uuid())
    assert again.status_code == 200, again.text
    assert again.json()["namespace_id"] == ns_id


# --- an archived id ----------------------------------------------------------


@pytest.mark.asyncio
async def test_an_archived_id_is_adopted_and_activates_only_its_own_orphans(client, auth_headers):
    prefix, archived, other = _p("dr"), _uuid(), _uuid()
    await _orphan(prefix, "mine", archived)
    await _orphan(prefix, "not-mine", other)
    created = await _upsert(client, auth_headers, prefix, namespace_id=archived)
    assert created.status_code == 200, created.text
    assert created.json()["namespace_id"] == archived
    assert created.json()["activated_orphan_grants"] == [f"{prefix}/group:mine"]
    assert not await _is_orphan(prefix, "mine")
    assert await _is_orphan(prefix, "not-mine")


@pytest.mark.asyncio
async def test_a_plain_create_activates_no_orphan(client, auth_headers):
    """Orphans on the name made for another namespace stay orphaned, even
    when a platform admin creates the name."""
    prefix = _p("plain")
    await _orphan(prefix, "older", _uuid())
    created = await _upsert(client, auth_headers, prefix)
    assert created.json()["activated_orphan_grants"] == []
    assert await _is_orphan(prefix, "older")


@pytest.mark.asyncio
async def test_an_archived_id_onto_a_name_reserved_for_another_is_refused(client, auth_headers):
    prefix = _p("taken")
    await _key(client, auth_headers, _p("owner"), [prefix], reserve=[{"prefix": prefix}])
    resp = await _upsert(client, auth_headers, prefix, namespace_id=_uuid())
    assert resp.status_code == 409, resp.text
    assert "reserved here" in resp.json()["detail"]
    assert await Namespace.find_one(Namespace.prefix == prefix) is None


@pytest.mark.asyncio
async def test_an_archived_id_onto_its_own_reservation_is_adopted(client, auth_headers):
    prefix = _p("same")
    await _key(client, auth_headers, _p("owner"), [prefix], reserve=[{"prefix": prefix}])
    reserved = await _reserved_id(prefix)
    resp = await _upsert(client, auth_headers, prefix, namespace_id=reserved)
    assert resp.status_code == 200, resp.text
    assert resp.json()["namespace_id"] == reserved


@pytest.mark.asyncio
async def test_an_archived_id_held_by_a_live_namespace_makes_a_copy(client, auth_headers):
    original, copy = _p("orig"), _p("copy")
    held = (await _upsert(client, auth_headers, original)).json()["namespace_id"]
    resp = await _upsert(client, auth_headers, copy, namespace_id=held)
    assert resp.status_code == 200, resp.text
    assert resp.json()["namespace_id"] not in (held, None)
    assert any(original in w for w in resp.json()["warnings"])


@pytest.mark.asyncio
async def test_only_a_platform_admin_may_send_an_id(client, auth_headers):
    prefix, owner = _p("app"), _p("owner")
    owner_key = await _key(client, auth_headers, owner, [prefix], reserve=[{"prefix": prefix}])
    resp = await _upsert(client, owner_key, prefix, namespace_id=_uuid())
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_a_dry_run_reports_the_id_and_writes_nothing(client, auth_headers):
    prefix, archived = _p("dry"), _uuid()
    resp = await client.put(
        f"{NS}/{prefix}", json={"namespace_id": archived}, params={"dry_run": "true"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["namespace_id"] == archived
    assert await Namespace.find_one(Namespace.prefix == prefix) is None


# --- the reservation holder (decision D2) ------------------------------------


@pytest.mark.asyncio
async def test_a_reservation_holders_create_activates_the_orphans_made_for_it(client, auth_headers):
    prefix, owner = _p("app"), _p("owner")
    owner_key = await _key(client, auth_headers, owner, [prefix], reserve=[{"prefix": prefix}])
    await _orphan(prefix, "for-this-one", await _reserved_id(prefix))
    await _orphan(prefix, "for-another", _uuid())
    created = await _upsert(client, owner_key, prefix)
    assert created.status_code == 200, created.text
    assert created.json()["activated_orphan_grants"] == [f"{prefix}/group:for-this-one"]
    assert await _is_orphan(prefix, "for-another")


# --- deletion ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_reservation_that_outlives_its_namespace_reserves_a_new_one(client, auth_headers):
    prefix, owner = _p("gone"), _p("owner")
    owner_key = await _key(client, auth_headers, owner, [prefix], reserve=[{"prefix": prefix}])
    first = (await _upsert(client, owner_key, prefix)).json()["namespace_id"]
    await Namespace.get_motor_collection().update_one({"prefix": prefix}, {"$set": {"deletion_mode": "full"}})
    deleted = await client.delete(f"{NS}/{prefix}", headers=auth_headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["status"] == "completed"
    assert await _reserved_id(prefix) not in (first, None)
    again = await _upsert(client, owner_key, prefix)
    assert again.json()["namespace_id"] not in (first, None)


# --- system restore (decision D3) ------------------------------------------------


async def _restore(client, headers, archive, **params):
    query = {k: str(v).lower() for k, v in {"dry_run": False, **params}.items()}
    return await client.post(RESTORE, json=archive, params=query, headers=headers)


@pytest.mark.asyncio
async def test_a_grant_for_another_namespace_of_the_same_name_is_orphaned(client, auth_headers):
    prefix = _p("same-name")
    await _upsert(client, auth_headers, prefix)
    archive = (await client.post(BACKUP, headers=auth_headers)).json()
    archive["grants"].append({
        "namespace": prefix, "subject": "theirs", "subject_type": "group",
        "permission": "admin", "namespace_id": _uuid(),
    })
    resp = await _restore(client, auth_headers, archive)
    assert resp.status_code == 200, resp.text
    assert f"{prefix}/group:theirs" in resp.json()["grants"]["orphaned"]
    assert await _is_orphan(prefix, "theirs")


@pytest.mark.asyncio
async def test_a_backup_carries_the_ids(client, auth_headers):
    prefix = _p("carried")
    ns_id = (await _upsert(client, auth_headers, prefix)).json()["namespace_id"]
    await client.post(f"{NS}/{prefix}/grants", headers=auth_headers, json=[
        {"subject": "readers", "subject_type": "group", "permission": "read"},
    ])
    archive = (await client.post(BACKUP, headers=auth_headers)).json()
    grant = next(g for g in archive["grants"] if g["namespace"] == prefix)
    assert grant["namespace_id"] == ns_id


@pytest.mark.asyncio
async def test_an_archive_without_namespace_ids_is_refused(client, auth_headers):
    archive = (await client.post(BACKUP, headers=auth_headers)).json()
    archive["grants"].append({
        "namespace": _p("old"), "subject": "g", "subject_type": "group", "permission": "read",
    })
    resp = await _restore(client, auth_headers, archive)
    assert resp.status_code == 422, resp.text
    assert "namespace_id" in resp.text
    archive["grants"].pop()
    archive["reservations"].append({"prefix": _p("old"), "app": "someone"})
    resp = await _restore(client, auth_headers, archive)
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_the_migration_gives_ids_and_drops_orphans_it_cannot_match(client):
    from registry.services.namespace_identity import migrate_namespace_ids

    with_ns, alone = _p("old-ns"), _p("old-resv")
    await Namespace(prefix=with_ns).insert()
    await RuntimeReservation(prefix=with_ns, app="a").insert()
    await RuntimeReservation(prefix=alone, app="b").insert()
    await _orphan(with_ns, "unknowable", None)
    await NamespaceGrant(
        namespace=with_ns, subject="effective", subject_type="group", permission="read",
        granted_by="t",
    ).insert()

    counts = await migrate_namespace_ids()
    assert counts["namespaces"] >= 1 and counts["reservations"] >= 2 and counts["orphans_deleted"] >= 1
    ns = await Namespace.find_one(Namespace.prefix == with_ns)
    assert ns.namespace_id and await _reserved_id(with_ns) == ns.namespace_id
    assert await _reserved_id(alone) not in (None, ns.namespace_id)
    assert await NamespaceGrant.find_one({"namespace": with_ns, "subject": "unknowable"}) is None
    assert await NamespaceGrant.find_one({"namespace": with_ns, "subject": "effective"}) is not None

    assert await migrate_namespace_ids() == {"namespaces": 0, "reservations": 0, "orphans_deleted": 0}
    assert (await Namespace.find_one(Namespace.prefix == with_ns)).namespace_id == ns.namespace_id


# --- the install's reservations file -----------------------------------------


@pytest.mark.asyncio
async def test_the_files_takeover_of_a_runtime_reservation_reserves_a_new_namespace(
    client, auth_headers, monkeypatch, tmp_path,
):
    from registry.services import key_import
    from registry.services.reservations import RESERVATIONS_FILE_ENV

    taken, existing = _p("taken"), _p("existing")
    await _key(client, auth_headers, _p("owner"), [taken], reserve=[{"prefix": taken}])
    runtime_id = await _reserved_id(taken)
    ns_id = (await _upsert(client, auth_headers, existing)).json()["namespace_id"]

    path = tmp_path / "namespace-reservations.json"
    path.write_text(json.dumps({"reservations": [
        {"prefix": taken, "app": "cooking"}, {"prefix": existing, "app": "cooking"},
    ]}))
    monkeypatch.setenv(RESERVATIONS_FILE_ENV, str(path))
    await key_import.run_pass()

    # The install's reservation of an absent namespace holds no id (CASE-1303):
    # the runtime key's id is not inherited, and none is minted in its place.
    assert runtime_id is not None
    assert await _reserved_id(taken) is None
    assert await _reserved_id(existing) == ns_id
    # A second pass with the same file changes nothing.
    await key_import.run_pass()
    assert await _reserved_id(taken) is None
