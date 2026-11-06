"""A scoped system backup and restore never act outside their scope (CASE-1071).

Review findings on the scoped archive (CASE-1065), written as the behaviour
the scope promises: "everything outside it is untouched, whatever the archive
holds".

- Finding 3: a key the scope touches only through its own namespaces (no
  grant, no reservation) and cannot carry is named in `keys_left_out`, so a
  scoped mirror never removes it, even where its copy here fits the scope.
- Finding 4: a scoped restore never replaces a key whose copy here reaches
  outside the scope.
- Finding 5: an empty scope is refused, never read as "the whole stack".
- Finding 7: a scoped archive's records outside its own scope are never
  written.
"""

import json
import uuid

import pytest

from registry.models.api_key import StoredAPIKey
from registry.models.grant import NamespaceGrant
from registry.services import attachments as attach_service
from registry.services import key_shadowing

KEYS = "/api/registry/api-keys"
NS = "/api/registry/namespaces"
BACKUP = "/api/registry/system-backup"
RESTORE = "/api/registry/system-restore"


def _p(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def fresh(client, tmp_path, monkeypatch):
    await NamespaceGrant.delete_all()
    key_shadowing.reset_for_tests()
    path = tmp_path / "attach-policy.json"
    path.write_text(json.dumps({"blocked_hosts": [], "reserved_names": []}))
    monkeypatch.setenv(attach_service.ATTACH_POLICY_FILE_ENV, str(path))
    yield client
    key_shadowing.reset_for_tests()


async def _ns(client, headers, prefix):
    assert (await client.put(f"{NS}/{prefix}", json={}, headers=headers)).status_code == 200


async def _key(client, headers, name, namespaces, **extra):
    r = await client.post(KEYS, headers=headers, json={"name": name, "namespaces": namespaces, **extra})
    assert r.status_code == 201, r.text


async def _backup(client, headers, **params) -> dict:
    r = await client.post(BACKUP, params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _restore(client, headers, archive, **params) -> dict:
    q = {k: str(v).lower() for k, v in params.items()}
    r = await client.post(RESTORE, json=archive, params=q, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _set_namespaces(name: str, namespaces: list[str] | None) -> None:
    await StoredAPIKey.find_one(StoredAPIKey.name == name).update({"$set": {"namespaces": namespaces}})


async def _namespaces(name: str) -> list[str] | None:
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    assert doc is not None, f"key {name} was removed"
    return doc.namespaces


async def _two_namespaces(client, headers) -> tuple[str, str]:
    a, z = _p("a"), _p("z")
    await _ns(client, headers, a)
    await _ns(client, headers, z)
    return a, z


# ── Finding 3 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_spanning_key_with_no_grant_is_left_out(fresh, auth_headers):
    client = fresh
    a, z = await _two_namespaces(client, auth_headers)
    k = _p("spans-bare")
    await _key(client, auth_headers, k, [a, z])  # no grant, no reservation

    archive = await _backup(client, auth_headers, namespaces=a)
    assert k not in {x["name"] for x in archive["api_keys"]}
    assert k in archive["source"]["scope"]["keys_left_out"]


@pytest.mark.asyncio
async def test_a_scoped_mirror_keeps_a_spanning_key_with_no_grant(fresh, auth_headers):
    """The key spans the scope on the source and fits it here: the archive's
    word decides, so the mirror must leave it alone."""
    client = fresh
    a, z = await _two_namespaces(client, auth_headers)
    k = _p("spans-bare")
    await _key(client, auth_headers, k, [a, z])
    archive = await _backup(client, auth_headers, namespaces=a)
    await _set_namespaces(k, [a])

    report = await _restore(client, auth_headers, archive, mode="mirror")
    assert k not in report["api_keys"]["deleted"]


@pytest.mark.asyncio
async def test_a_scoped_mirror_keeps_an_unscoped_key_with_no_grant(fresh, auth_headers):
    client = fresh
    a, _ = await _two_namespaces(client, auth_headers)
    k = _p("unscoped-bare")
    await _key(client, auth_headers, k, None, groups=["wip-services"])
    archive = await _backup(client, auth_headers, namespaces=a)
    assert k in archive["source"]["scope"]["keys_left_out"]
    await _set_namespaces(k, [a])

    report = await _restore(client, auth_headers, archive, mode="mirror")
    assert k not in report["api_keys"]["deleted"]


# ── Finding 4 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["overwrite", "mirror"])
async def test_a_scoped_restore_never_replaces_a_key_that_reaches_outside_here(fresh, auth_headers, mode):
    client = fresh
    a, z = await _two_namespaces(client, auth_headers)
    k = _p("in-a")
    await _key(client, auth_headers, k, [a])
    archive = await _backup(client, auth_headers, namespaces=a)
    assert k in {x["name"] for x in archive["api_keys"]}
    await _set_namespaces(k, [a, z])  # here the key also reaches z

    report = await _restore(client, auth_headers, archive, mode=mode)
    assert k not in report["api_keys"]["replaced"]
    applied = await _restore(client, auth_headers, archive, mode=mode, dry_run=False,
                             expect=report["plan_digest"])
    assert k not in applied["api_keys"]["replaced"]
    assert sorted(await _namespaces(k)) == sorted([a, z])
    skipped = {x["name"]: x["reason_code"] for x in applied["api_keys"]["skipped"]}
    assert skipped[k] == "out_of_scope_here"


# ── Finding 5 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{"namespaces": ""}, {"namespaces": " , "}, {"attachments": ""}])
async def test_an_empty_scope_is_refused(fresh, auth_headers, params):
    r = await fresh.post(BACKUP, params=params, headers=auth_headers)
    assert r.status_code == 422, r.text


# ── Finding 7 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_scoped_archive_never_writes_a_grant_outside_its_scope(fresh, auth_headers):
    client = fresh
    a, z = await _two_namespaces(client, auth_headers)
    archive = await _backup(client, auth_headers, namespaces=a)
    archive["grants"].append(
        {"namespace": z, "subject": "edited-in", "subject_type": "group", "permission": "admin"}
    )

    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert f"{z}/group:edited-in" not in report["grants"]["created"]
    assert await NamespaceGrant.find_one({"namespace": z, "subject": "edited-in"}) is None
    codes = {(x["namespace"], x["subject"]): x["reason_code"] for x in report["grants"]["skipped"]}
    assert codes[(z, "edited-in")] == "outside_archive_scope"


@pytest.mark.asyncio
async def test_a_scoped_archive_never_writes_any_record_outside_its_scope(fresh, auth_headers):
    """Keys, reservations and attachments too: the archive's own records are
    checked against the scope it declares, so an edited archive cannot reach
    past it."""
    client = fresh
    a, z = await _two_namespaces(client, auth_headers)
    archive = await _backup(client, auth_headers, namespaces=a)
    outside_key, outside_app = _p("edited-key"), _p("edited-app")
    archive["api_keys"].append({"name": outside_key, "key_hash": "x" * 60, "namespaces": [z]})
    archive["reservations"].append({"prefix": _p("edited-ns"), "app": "someone"})
    archive["attachments"].append({
        "name": outside_app, "upstream": "https://edited.apps.example", "auth_required": True,
        "allowed_groups": [], "status": "active",
    })

    report = await _restore(client, auth_headers, archive, dry_run=False)
    for section, ident in (
        ("api_keys", outside_key),
        ("reservations", archive["reservations"][-1]["prefix"]),
        ("attachments", outside_app),
    ):
        codes = {x["id"]: x["reason_code"] for x in report[section]["skipped"]}
        assert codes.get(ident) == "outside_archive_scope", (section, report[section])
        assert ident not in report[section]["created"]
    assert await StoredAPIKey.find_one(StoredAPIKey.name == outside_key) is None


# ── CASE-1073 B: grants outside the scope ─────────────────────────────


async def _grant(client, headers, ns, subject, subject_type="api_key", permission="admin"):
    r = await client.post(f"{NS}/{ns}/grants", headers=headers, json=[
        {"subject": subject, "subject_type": subject_type, "permission": permission},
    ])
    assert r.json()["results"][0]["status"] in ("created", "updated"), r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["bare", "apikey:"])
async def test_a_scoped_mirror_keeps_a_key_with_a_grant_outside_the_scope(fresh, auth_headers, spelling):
    """The key exists only here, holds [a], and has a grant on z. Its grant on
    z gives no access today (a key's access is bounded by its namespaces), but
    it is a record outside the scope: removing the key would delete it."""
    client = fresh
    a, z = await _two_namespaces(client, auth_headers)
    archive = await _backup(client, auth_headers, namespaces=a)
    k = _p("here-only")
    await _key(client, auth_headers, k, [a])
    await _grant(client, auth_headers, z, k if spelling == "bare" else f"apikey:{k}")

    report = await _restore(client, auth_headers, archive, mode="mirror")
    assert k not in report["api_keys"]["deleted"]
    assert not [g for g in report["grants"]["deleted"] if g.startswith(f"{z}/")]
    applied = await _restore(client, auth_headers, archive, mode="mirror", dry_run=False,
                             expect=report["plan_digest"])
    assert k not in applied["api_keys"]["deleted"]
    assert await StoredAPIKey.find_one(StoredAPIKey.name == k) is not None
    assert await NamespaceGrant.find_one({"namespace": z}) is not None


@pytest.mark.asyncio
async def test_a_scoped_mirror_still_removes_a_key_whose_grants_are_all_inside(fresh, auth_headers):
    client = fresh
    a, _ = await _two_namespaces(client, auth_headers)
    archive = await _backup(client, auth_headers, namespaces=a)
    k = _p("here-only")
    await _key(client, auth_headers, k, [a])
    await _grant(client, auth_headers, a, k)

    report = await _restore(client, auth_headers, archive, mode="mirror")
    assert k in report["api_keys"]["deleted"]


# ── Keys in a stack-wide group ────────────────────────────────────────
#
# A wip-admins key is admin on every namespace before its namespace list is
# consulted, and a wip-services key is trusted as a service everywhere. Such a
# key reaches outside every scope, whatever its list says, so a scoped backup
# never carries it and a scoped restore never writes, replaces or removes it.

STACK_WIDE = pytest.mark.parametrize("group", ["wip-admins", "wip-services"])


async def _groups(name: str) -> list[str] | None:
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    return None if doc is None else sorted(doc.groups)


async def _set_groups(name: str, groups: list[str]) -> None:
    await StoredAPIKey.find_one(StoredAPIKey.name == name).update({"$set": {"groups": groups}})


def test_the_stack_wide_groups_are_the_registrys_privileged_groups():
    from registry.api.grants import _PRIVILEGED_GROUPS
    from registry.services.system_backup import _STACK_WIDE_GROUPS

    assert _STACK_WIDE_GROUPS == _PRIVILEGED_GROUPS


@pytest.mark.asyncio
@STACK_WIDE
async def test_a_scoped_backup_never_carries_a_stack_wide_key(fresh, auth_headers, group):
    client = fresh
    a, _ = await _two_namespaces(client, auth_headers)
    k = _p("stack-wide")
    await _key(client, auth_headers, k, [a], groups=[group])

    archive = await _backup(client, auth_headers, namespaces=a)
    assert k not in {x["name"] for x in archive["api_keys"]}
    assert k in archive["source"]["scope"]["keys_left_out"]


@pytest.mark.asyncio
@STACK_WIDE
async def test_a_scoped_mirror_never_removes_a_stack_wide_key(fresh, auth_headers, group):
    client = fresh
    a, _ = await _two_namespaces(client, auth_headers)
    archive = await _backup(client, auth_headers, namespaces=a)
    k = _p("stack-wide-here")
    await _key(client, auth_headers, k, [a], groups=[group])  # only on this stack

    report = await _restore(client, auth_headers, archive, mode="mirror")
    assert k not in report["api_keys"]["deleted"]
    applied = await _restore(client, auth_headers, archive, mode="mirror", dry_run=False,
                             expect=report["plan_digest"])
    assert k not in applied["api_keys"]["deleted"]
    assert await _groups(k) == [group]


@pytest.mark.asyncio
@STACK_WIDE
@pytest.mark.parametrize("mode", ["overwrite", "mirror"])
async def test_a_scoped_restore_never_takes_a_stack_wide_group_away(fresh, auth_headers, group, mode):
    client = fresh
    a, _ = await _two_namespaces(client, auth_headers)
    k = _p("keeps-group")
    await _key(client, auth_headers, k, [a])
    archive = await _backup(client, auth_headers, namespaces=a)
    assert k in {x["name"] for x in archive["api_keys"]}
    await _set_groups(k, [group])  # here the key is stack-wide

    report = await _restore(client, auth_headers, archive, mode=mode)
    applied = await _restore(client, auth_headers, archive, mode=mode, dry_run=False,
                             expect=report["plan_digest"])
    assert k not in applied["api_keys"]["replaced"]
    skipped = {x["name"]: x["reason_code"] for x in applied["api_keys"]["skipped"]}
    assert skipped[k] == "out_of_scope_here"
    assert await _groups(k) == [group]


@pytest.mark.asyncio
@STACK_WIDE
@pytest.mark.parametrize("mode", ["upsert", "overwrite", "mirror"])
async def test_a_scoped_archive_carrying_a_stack_wide_key_never_writes_it(fresh, auth_headers, group, mode):
    """An archive made before this rule (or edited) may carry such a key. Its
    restore never grants the group: not to a new key, not to an existing one."""
    client = fresh
    a, _ = await _two_namespaces(client, auth_headers)
    k = _p("gains-group")
    await _key(client, auth_headers, k, [a])
    archive = await _backup(client, auth_headers, namespaces=a)
    for x in archive["api_keys"]:
        if x["name"] == k:
            x["groups"] = [group]
    new = _p("new-stack-wide")
    archive["api_keys"].append({**next(x for x in archive["api_keys"] if x["name"] == k), "name": new})

    report = await _restore(client, auth_headers, archive, mode=mode)
    params = {"expect": report["plan_digest"]} if mode == "mirror" else {}
    applied = await _restore(client, auth_headers, archive, mode=mode, dry_run=False, **params)
    skipped = {x["name"]: x["reason_code"] for x in applied["api_keys"]["skipped"]}
    assert skipped[k] == skipped[new] == "outside_archive_scope"
    assert await _groups(k) == []
    assert await _groups(new) is None
