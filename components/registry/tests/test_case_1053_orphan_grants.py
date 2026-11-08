"""Orphaned grants: the system restore and the data restore in either order,
and a reservation that stays its owner's approval to create the namespace.

A system restore writes a grant whose namespace is not here (no namespace or
reservation holds the namespace id the grant was archived with) as
`orphaned`. An orphan gives nothing. It becomes effective when a namespace
with its id is created (the ID-preserving data restore does), when a
namespace admin activates it, or when an admin grants it again. A key that
reserves the prefix later and creates the namespace does not inherit it: that
is another namespace, with another id.

The owner of a runtime reservation (the key that reserved the prefix) may
create the namespace without holding a grant, and gets its `admin` grant back
when it does: deleting a namespace purges its grants but keeps the reservation.
"""

import uuid

import pytest

from registry.models.grant import NamespaceGrant
from registry.models.namespace import Namespace
from registry.models.reservation import RuntimeReservation

KEYS = "/api/registry/api-keys"
NS = "/api/registry/namespaces"
GRANTS = "/api/registry/grants"
BACKUP = "/api/registry/system-backup"
RESTORE = "/api/registry/system-restore"


def _p(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


async def _key(client, headers, name, namespaces, **extra) -> dict:
    resp = await client.post(KEYS, headers=headers, json={"name": name, "namespaces": namespaces, **extra})
    assert resp.status_code == 201, resp.text
    return {"X-API-Key": resp.json()["plaintext_key"]}


async def _restore(client, headers, archive, **params) -> dict:
    query = {k: str(v).lower() for k, v in params.items()}
    resp = await client.post(RESTORE, json=archive, params=query, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _archive_with_grant(
    client, headers, namespace, subject, permission="write", namespace_id=None,
) -> dict:
    """A system backup of this instance plus one key grant on `namespace`,
    archived for the namespace `namespace_id` (by default: the one holding
    the name here, else a namespace this instance has never seen)."""
    resp = await client.post(BACKUP, headers=headers)
    assert resp.status_code == 200, resp.text
    archive = resp.json()
    archive["grants"].append({
        "namespace": namespace, "subject": subject, "subject_type": "api_key",
        "permission": permission, "granted_by": "someone",
        "namespace_id": namespace_id or await _held_id(namespace) or str(uuid.uuid4()),
    })
    return archive


async def _held_id(prefix: str) -> str | None:
    ns = await Namespace.find_one(Namespace.prefix == prefix)
    if ns is not None:
        return ns.namespace_id
    r = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
    return r.namespace_id if r is not None else None


async def _permission(client, key_headers, prefix) -> int | str:
    resp = await client.get(f"/api/registry/my/namespaces/{prefix}/permission", headers=key_headers)
    return resp.json()["permission"] if resp.status_code == 200 else resp.status_code


async def _drop_namespace(prefix: str) -> None:
    """What a namespace deletion leaves behind: the namespace and every grant
    on it gone, its reservation kept."""
    await Namespace.find({"prefix": prefix}).delete()
    await NamespaceGrant.find({"namespace": prefix}).delete()


@pytest.mark.asyncio
async def test_system_restore_first_then_the_data_restore_activates(client, auth_headers):
    """The disaster-recovery order the guide used to forbid: system first,
    then the data restore (re-creating the namespace with its archived id)."""
    prefix, reader = _p("later"), _p("reader")
    reader_key = await _key(client, auth_headers, reader, [prefix])
    archived_id = str(uuid.uuid4())
    archive = await _archive_with_grant(client, auth_headers, prefix, reader, namespace_id=archived_id)

    dry = await _restore(client, auth_headers, archive)
    gid = f"{prefix}/api_key:{reader}"
    assert gid in dry["grants"]["created"] and gid in dry["grants"]["orphaned"]
    assert await NamespaceGrant.find_one({"namespace": prefix}) is None  # dry run wrote nothing

    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert report["grants"]["orphaned"] == [gid]
    stored = await NamespaceGrant.find_one({"namespace": prefix, "subject": reader})
    assert stored.orphaned and stored.orphaned_at is not None
    assert stored.namespace_id == archived_id

    created = await client.put(f"{NS}/{prefix}", json={"namespace_id": archived_id}, headers=auth_headers)
    assert created.status_code == 200, created.text
    assert created.json()["namespace_id"] == archived_id
    assert created.json()["activated_orphan_grants"] == [gid]
    assert await _permission(client, reader_key, prefix) == "write"

    # Re-running the system restore now finds the grant as it is.
    again = await _restore(client, auth_headers, archive, dry_run=False)
    assert gid in again["grants"]["unchanged"]


@pytest.mark.asyncio
async def test_an_orphan_gives_nothing(client, auth_headers):
    """Even on a namespace that exists, an orphan resolves to no access."""
    prefix, reader = _p("ns"), _p("reader")
    reader_key = await _key(client, auth_headers, reader, [prefix])
    assert (await client.put(f"{NS}/{prefix}", json={}, headers=auth_headers)).status_code == 200
    await NamespaceGrant(
        namespace=prefix, subject=reader, subject_type="api_key", permission="admin",
        granted_by="t", orphaned=True,
    ).insert()
    # The key's scope alone gives read; the orphaned admin adds nothing.
    assert await _permission(client, reader_key, prefix) == "read"
    assert (await client.get(f"{NS}/{prefix}/grants", headers=reader_key)).status_code == 403


@pytest.mark.asyncio
async def test_a_later_reservation_holder_does_not_inherit_orphans(client, auth_headers):
    prefix, reader, owner = _p("taken"), _p("reader"), _p("owner")
    reader_key = await _key(client, auth_headers, reader, [prefix])
    archive = await _archive_with_grant(client, auth_headers, prefix, reader, "admin")
    await _restore(client, auth_headers, archive, dry_run=False)

    owner_key = await _key(client, auth_headers, owner, [prefix], reserve=[{"prefix": prefix}])
    created = await client.put(f"{NS}/{prefix}", json={}, headers=owner_key)
    assert created.status_code == 200, created.text
    assert created.json()["activated_orphan_grants"] == []
    assert await _permission(client, reader_key, prefix) == "read"
    assert await _permission(client, owner_key, prefix) == "admin"

    # Re-running the system restore leaves the orphan as it is: the namespace
    # here has another id, so the archive's grant is still not its own.
    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert f"{prefix}/api_key:{reader}" in report["grants"]["unchanged"]

    # The namespace's admin adopts it explicitly.
    resp = await client.post(f"{NS}/{prefix}/grants/activate", headers=owner_key, json=[
        {"subject": reader, "subject_type": "api_key"},
        {"subject": owner, "subject_type": "api_key"},
        {"subject": "nobody", "subject_type": "group"},
    ])
    assert resp.status_code == 200, resp.text
    assert [r["status"] for r in resp.json()["results"]] == ["activated", "unchanged", "not_found"]
    assert await _permission(client, reader_key, prefix) == "admin"


@pytest.mark.asyncio
async def test_activate_needs_admin_and_an_existing_namespace(client, auth_headers):
    prefix, reader = _p("ns"), _p("reader")
    reader_key = await _key(client, auth_headers, reader, [prefix])
    item = [{"subject": reader, "subject_type": "api_key"}]
    assert (await client.post(f"{NS}/{prefix}/grants/activate", headers=auth_headers, json=item)).status_code == 404
    assert (await client.put(f"{NS}/{prefix}", json={}, headers=auth_headers)).status_code == 200
    assert (await client.post(f"{NS}/{prefix}/grants/activate", headers=reader_key, json=item)).status_code == 403


@pytest.mark.asyncio
async def test_granting_again_activates_an_orphan(client, auth_headers):
    prefix, reader = _p("ns"), _p("reader")
    reader_key = await _key(client, auth_headers, reader, [prefix])
    archive = await _archive_with_grant(client, auth_headers, prefix, reader)
    await _restore(client, auth_headers, archive, dry_run=False)
    await Namespace(prefix=prefix, description="made without activation").insert()
    assert await _permission(client, reader_key, prefix) == "read"

    resp = await client.post(f"{NS}/{prefix}/grants", headers=auth_headers, json=[
        {"subject": reader, "subject_type": "api_key", "permission": "write"},
    ])
    assert resp.json()["results"][0]["status"] == "updated"
    assert await _permission(client, reader_key, prefix) == "write"


@pytest.mark.asyncio
async def test_orphans_are_listed_for_platform_admins(client, auth_headers):
    prefix, reader = _p("lost"), _p("reader")
    reader_key = await _key(client, auth_headers, reader, [prefix])
    archive = await _archive_with_grant(client, auth_headers, prefix, reader)
    await _restore(client, auth_headers, archive, dry_run=False)

    resp = await client.get(GRANTS, params={"orphaned": "true", "namespace": prefix}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1 and body["pages"] == 1
    assert body["items"][0]["subject"] == reader and body["items"][0]["orphaned"] is True

    effective = await client.get(GRANTS, params={"orphaned": "false", "namespace": prefix}, headers=auth_headers)
    assert effective.json()["total"] == 0
    assert (await client.get(GRANTS, headers=reader_key)).status_code == 403


@pytest.mark.asyncio
async def test_an_archived_orphan_stays_orphaned_where_its_prefix_exists(client, auth_headers):
    """A backup taken while a grant was orphaned carries that, so restoring it
    onto an instance where the prefix exists (perhaps an unrelated namespace)
    does not hand an old grant live access."""
    prefix, reader = _p("ns"), _p("reader")
    reader_key = await _key(client, auth_headers, reader, [prefix])
    archive = await _archive_with_grant(client, auth_headers, prefix, reader)
    await _restore(client, auth_headers, archive, dry_run=False)
    backup = (await client.post(BACKUP, headers=auth_headers)).json()
    carried = next(g for g in backup["grants"] if g["namespace"] == prefix)
    assert carried["orphaned"] is True

    await NamespaceGrant.find({"namespace": prefix}).delete()
    await Namespace(prefix=prefix, description="an unrelated namespace").insert()
    report = await _restore(client, auth_headers, backup, dry_run=False)
    assert f"{prefix}/api_key:{reader}" in report["grants"]["orphaned"]
    assert await _permission(client, reader_key, prefix) == "read"


@pytest.mark.asyncio
async def test_a_grant_on_an_existing_or_reserved_namespace_is_written_effective(client, auth_headers):
    existing, reserved, reader = _p("here"), _p("resv"), _p("reader")
    await _key(client, auth_headers, reader, [existing, reserved])
    assert (await client.put(f"{NS}/{existing}", json={}, headers=auth_headers)).status_code == 200
    await _key(client, auth_headers, _p("owner"), [reserved], reserve=[{"prefix": reserved}])
    archive = await _archive_with_grant(client, auth_headers, existing, reader)
    archive["grants"].append({
        **archive["grants"][-1], "namespace": reserved, "namespace_id": await _held_id(reserved),
    })
    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert report["grants"]["orphaned"] == []
    for prefix in (existing, reserved):
        stored = await NamespaceGrant.find_one({"namespace": prefix, "subject": reader})
        assert stored is not None and not stored.orphaned


# --- The reservation's owner (CASE-1046) ---


@pytest.mark.asyncio
async def test_the_owner_recreates_its_namespace_after_a_deletion(client, auth_headers):
    prefix, owner = _p("app"), _p("owner")
    owner_key = await _key(client, auth_headers, owner, [prefix], reserve=[{"prefix": prefix}])
    assert (await client.put(f"{NS}/{prefix}", json={}, headers=owner_key)).status_code == 200

    await _drop_namespace(prefix)
    assert await NamespaceGrant.find_one({"namespace": prefix}) is None

    again = await client.put(f"{NS}/{prefix}", json={}, headers=owner_key)
    assert again.status_code == 200, again.text
    grant = await NamespaceGrant.find_one({"namespace": prefix, "subject": owner})
    assert grant is not None and grant.permission == "admin"
    assert await _permission(client, owner_key, prefix) == "admin"
    # Settings rules are unchanged for the owner.
    other = await client.put(f"{NS}/{prefix}", json={"deletion_mode": "full"}, headers=owner_key)
    assert other.status_code == 403


@pytest.mark.asyncio
async def test_the_owner_keeps_a_narrowed_grant(client, auth_headers):
    """The owner's grant is re-created only when missing; one an admin
    narrowed stays as the admin left it."""
    prefix, owner = _p("app"), _p("owner")
    owner_key = await _key(client, auth_headers, owner, [prefix], reserve=[{"prefix": prefix}])
    await NamespaceGrant.find({"namespace": prefix, "subject": owner}).update({"$set": {"permission": "write"}})
    assert (await client.put(f"{NS}/{prefix}", json={}, headers=owner_key)).status_code == 200
    grant = await NamespaceGrant.find_one({"namespace": prefix, "subject": owner})
    assert grant.permission == "write"


@pytest.mark.asyncio
async def test_a_non_owner_admin_is_admitted_but_gets_no_grant(client, auth_headers):
    prefix, owner, other = _p("app"), _p("owner"), _p("other")
    await _key(client, auth_headers, owner, [prefix], reserve=[{"prefix": prefix}])
    other_key = await _key(client, auth_headers, other, [prefix], grant_permission="admin")
    await NamespaceGrant.find({"namespace": prefix, "subject": owner}).delete()

    assert (await client.put(f"{NS}/{prefix}", json={}, headers=other_key)).status_code == 200
    assert await NamespaceGrant.find_one({"namespace": prefix, "subject": owner}) is None
    assert {g.subject for g in await NamespaceGrant.find({"namespace": prefix}).to_list()} == {other}


@pytest.mark.asyncio
async def test_a_non_owner_without_admin_is_refused(client, auth_headers):
    prefix, owner, other = _p("app"), _p("owner"), _p("other")
    await _key(client, auth_headers, owner, [prefix], reserve=[{"prefix": prefix}])
    other_key = await _key(client, auth_headers, other, [prefix], grant_permission="write")
    resp = await client.put(f"{NS}/{prefix}", json={}, headers=other_key)
    assert resp.status_code == 403
    assert "requires admin" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_the_owner_admission_needs_an_api_key_identity(client, auth_headers):
    """A login whose name happens to equal the owning key's is not the key."""
    from registry.api.grants import reservation_owner
    from wip_auth import UserIdentity

    prefix, owner = _p("app"), _p("owner")
    await _key(client, auth_headers, owner, [prefix], reserve=[{"prefix": prefix}])
    scoped = {"namespaces": [prefix]}
    as_key = UserIdentity(user_id=f"apikey:{owner}", username=owner, auth_method="api_key",
                          raw_claims=scoped)
    as_login = UserIdentity(user_id=owner, username=owner, auth_method="jwt", raw_claims=scoped)
    out_of_scope = UserIdentity(user_id=f"apikey:{owner}", username=owner, auth_method="api_key",
                                raw_claims={"namespaces": []})
    unscoped = UserIdentity(user_id=f"apikey:{owner}", username=owner, auth_method="api_key")
    assert await reservation_owner(as_key, prefix) == owner
    assert await reservation_owner(as_login, prefix) is None
    assert await reservation_owner(out_of_scope, prefix) is None
    assert await reservation_owner(unscoped, prefix) is None


# --- Review CASE-1053#6 ---


@pytest.mark.asyncio
async def test_another_owners_reservation_does_not_inherit_the_archives_grants(client, auth_headers):
    """The archive's prefix is reserved here by someone else: that is another
    namespace of the same name, so the archive's grants on it are orphaned
    and its owner does not inherit them when it creates the namespace."""
    prefix, reader, owner = _p("foo"), _p("reader"), _p("owner")
    reader_key = await _key(client, auth_headers, reader, [prefix])
    owner_key = await _key(client, auth_headers, owner, [prefix], reserve=[{"prefix": prefix}])
    archived_id = str(uuid.uuid4())
    archive = await _archive_with_grant(
        client, auth_headers, prefix, reader, "admin", namespace_id=archived_id,
    )
    archive["reservations"] = [{"prefix": prefix, "app": "someone-else", "namespace_id": archived_id}]

    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert [s["id"] for s in report["reservations"]["skipped"]] == [prefix]
    assert f"{prefix}/api_key:{reader}" in report["grants"]["orphaned"]

    assert (await client.put(f"{NS}/{prefix}", json={}, headers=owner_key)).status_code == 200
    assert await _permission(client, reader_key, prefix) == "read"


@pytest.mark.asyncio
async def test_an_archived_namespace_counts_as_existing(client, auth_headers):
    prefix, reader = _p("arch"), _p("reader")
    await _key(client, auth_headers, reader, [prefix])
    assert (await client.put(f"{NS}/{prefix}", json={}, headers=auth_headers)).status_code == 200
    assert (await client.post(f"{NS}/{prefix}/archive", headers=auth_headers)).status_code == 200
    archive = await _archive_with_grant(client, auth_headers, prefix, reader)
    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert report["grants"]["orphaned"] == []
    stored = await NamespaceGrant.find_one({"namespace": prefix, "subject": reader})
    assert stored is not None and not stored.orphaned


@pytest.mark.asyncio
async def test_an_archived_orphan_never_replaces_an_effective_grant(client, auth_headers):
    """Neither demoted (access taken away) nor promoted (an orphan's higher
    permission handed out), even with overwrite."""
    prefix, reader = _p("ns"), _p("reader")
    reader_key = await _key(client, auth_headers, reader, [prefix])
    assert (await client.put(f"{NS}/{prefix}", json={}, headers=auth_headers)).status_code == 200
    await client.post(f"{NS}/{prefix}/grants", headers=auth_headers, json=[
        {"subject": reader, "subject_type": "api_key", "permission": "write"},
    ])
    archive = await _archive_with_grant(client, auth_headers, prefix, reader, "admin")
    archive["grants"] = [{**g, "orphaned": True} if g["namespace"] == prefix else g for g in archive["grants"]]

    report = await _restore(client, auth_headers, archive, dry_run=False, overwrite=True)
    reasons = {s["id"]: s["reason"] for s in report["grants"]["skipped"]}
    assert "effective here" in reasons[f"{prefix}/api_key:{reader}"]
    codes = {s["id"]: s["reason_code"] for s in report["grants"]["skipped"]}
    assert codes[f"{prefix}/api_key:{reader}"] == "orphaned_in_archive"
    assert await _permission(client, reader_key, prefix) == "write"


@pytest.mark.asyncio
async def test_the_owner_is_admin_on_its_absent_prefix_for_every_service(client, auth_headers):
    """A restore checks admin through check-permission before it creates the
    namespace, so the owner rule has to hold there too, while the namespace
    does not exist."""
    prefix, owner = _p("app"), _p("owner")
    await _key(client, auth_headers, owner, [prefix], reserve=[{"prefix": prefix}])
    await NamespaceGrant.find({"namespace": prefix}).delete()  # as a deletion leaves it

    async def check() -> str:
        resp = await client.get(
            "/api/registry/my/check-permission",
            params={"namespace": prefix, "user_id": f"apikey:{owner}", "username": owner,
                    "auth_method": "api_key"},
            headers={**auth_headers, "X-Key-Namespaces": prefix},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["permission"]

    assert await check() == "admin"
    # Once the namespace exists, its grants decide: an admin created it and
    # gave the owner nothing, so its scope gives read.
    await Namespace(prefix=prefix, description="made by an admin").insert()
    assert await check() == "read"


@pytest.mark.asyncio
async def test_narrowing_a_key_off_its_namespace_is_not_undone_by_a_deletion(client, auth_headers):
    """Narrowing keeps the reservation while the namespace exists. After the
    namespace is deleted the key must not come back as its owner (review
    CASE-1053#8)."""
    prefix, owner = _p("app"), _p("owner")
    owner_key = await _key(client, auth_headers, owner, [prefix], reserve=[{"prefix": prefix}])
    assert (await client.put(f"{NS}/{prefix}", json={}, headers=owner_key)).status_code == 200
    narrowed = await client.delete(f"{KEYS}/{owner}/namespaces/{prefix}", headers=auth_headers)
    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.json()["reservation"] == "kept"
    await _drop_namespace(prefix)

    assert (await client.put(f"{NS}/{prefix}", json={}, headers=owner_key)).status_code == 403
    resp = await client.get(
        "/api/registry/my/check-permission",
        params={"namespace": prefix, "user_id": f"apikey:{owner}", "username": owner,
                "auth_method": "api_key"},
        headers={**auth_headers, "X-Key-Namespaces": ""},
    )
    assert resp.json()["permission"] == "none"
