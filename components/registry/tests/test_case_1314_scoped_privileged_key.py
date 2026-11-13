"""A key in a privileged group (wip-admins, wip-services) is never scoped.

Such a key acts on every namespace. A namespaces list on it, an empty one
included, would scope it at the stores' per-namespace doors and not at the
Registry's platform doors, so every write path refuses the combination: the
API (create, update, widen), the key-file import and a system restore. A key
stored with both before the rule is reported, never rewritten.
"""

import uuid

import pytest

from registry.models.api_key import StoredAPIKey
from registry.services import key_import
from registry.services.key_import import CANARY_KEY_NAME
from tests.test_case_1033_key_import import _key_file
from tests.test_case_1056_mirror_restore import _backup, _restore
from tests.test_case_1191_system_backup import fresh  # noqa: F401  (fixture)
from wip_auth import ADMIN_GROUP, SERVICES_GROUP, privileged_scope_error

KEYS = "/api/registry/api-keys"


def _n(tag: str = "k") -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


async def _legacy_key(groups: list[str], namespaces: list[str] | None) -> str:
    """A key stored with both, as one made before the rule would be."""
    name = _n("legacy")
    await StoredAPIKey(
        name=name, key_hash="$2b$12$" + "x" * 53, groups=groups, namespaces=namespaces,
    ).insert()
    return name


def test_the_rule():
    assert privileged_scope_error([SERVICES_GROUP], ["a"])
    assert privileged_scope_error([ADMIN_GROUP], [])  # an empty list scopes too
    assert privileged_scope_error(["wip-users", ADMIN_GROUP], ["a"])
    assert privileged_scope_error([SERVICES_GROUP], None) is None
    assert privileged_scope_error(["wip-users"], ["a"]) is None
    assert privileged_scope_error(None, ["a"]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [ADMIN_GROUP, SERVICES_GROUP])
@pytest.mark.parametrize("namespaces", [["default"], []])
async def test_create_refuses_a_scoped_privileged_key(client, auth_headers, group, namespaces):
    name = _n()
    r = await client.post(
        KEYS, headers=auth_headers, json={"name": name, "groups": [group], "namespaces": namespaces},
    )
    assert r.status_code == 422, r.text
    assert "groups and namespaces" in r.json()["detail"]
    assert await StoredAPIKey.find_one(StoredAPIKey.name == name) is None


@pytest.mark.asyncio
async def test_create_takes_either_alone(client, auth_headers):
    r = await client.post(KEYS, headers=auth_headers, json={"name": _n(), "groups": [SERVICES_GROUP]})
    assert r.status_code == 201, r.text
    r = await client.post(
        KEYS, headers=auth_headers, json={"name": _n(), "groups": ["wip-users"], "namespaces": ["default"]},
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_update_refuses_a_change_that_leaves_both(client, auth_headers):
    scoped = _n()
    assert (await client.post(KEYS, headers=auth_headers, json={"name": scoped, "namespaces": ["default"]})).status_code == 201
    r = await client.patch(f"{KEYS}/{scoped}", headers=auth_headers, json={"groups": [SERVICES_GROUP]})
    assert r.status_code == 422, r.text

    privileged = _n()
    assert (await client.post(KEYS, headers=auth_headers, json={"name": privileged, "groups": [ADMIN_GROUP]})).status_code == 201
    r = await client.patch(f"{KEYS}/{privileged}", headers=auth_headers, json={"namespaces": ["default"]})
    assert r.status_code == 422, r.text

    for name, groups, namespaces in ((scoped, [], ["default"]), (privileged, [ADMIN_GROUP], None)):
        doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
        assert (doc.groups, doc.namespaces) == (groups, namespaces)


@pytest.mark.asyncio
async def test_a_legacy_key_can_be_disabled_and_fixed_but_not_widened(client, auth_headers):
    name = await _legacy_key([SERVICES_GROUP], ["default"])
    r = await client.patch(f"{KEYS}/{name}", headers=auth_headers, json={"enabled": False})
    assert r.status_code == 200, r.text
    r = await client.put(f"{KEYS}/{name}/namespaces/other", headers=auth_headers, json={})
    assert r.status_code == 422, r.text
    assert (await StoredAPIKey.find_one(StoredAPIKey.name == name)).namespaces == ["default"]
    r = await client.patch(f"{KEYS}/{name}", headers=auth_headers, json={"groups": []})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_legacy_keys_are_reported_not_rewritten(client, auth_headers):
    name = await _legacy_key([ADMIN_GROUP], [])
    assert name in await key_import.scoped_privileged_keys()
    health = (await client.get("/health")).json()
    assert name in health["scoped_privileged_keys"]
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    assert (doc.groups, doc.namespaces) == ([ADMIN_GROUP], [])


@pytest.mark.asyncio
async def test_the_key_file_import_refuses_a_file_holding_one(client, monkeypatch, tmp_path):
    name = _n("filekey")
    _key_file(monkeypatch, tmp_path, {
        "name": name, "key": "file-plaintext-1314-0001", "groups": [SERVICES_GROUP],
        "namespaces": ["default"],
    })
    report = await key_import.run_pass()
    assert key_import.key_file_state() == "refused"
    assert any(name in why and SERVICES_GROUP in why for why in report.skipped), report.skipped
    assert await StoredAPIKey.find_one(StoredAPIKey.name == name) is None
    assert CANARY_KEY_NAME not in report.keys_created


@pytest.mark.asyncio
async def test_a_restore_skips_one_and_keeps_the_key_here(fresh, auth_headers):  # noqa: F811
    client = fresh
    name = _n()
    assert (await client.post(KEYS, headers=auth_headers, json={"name": name, "namespaces": ["default"]})).status_code == 201
    archive = await _backup(client, auth_headers)
    for k in archive["api_keys"]:
        if k["name"] == name:
            k["groups"] = [SERVICES_GROUP]
    report = await _restore(client, auth_headers, archive, dry_run=False, overwrite=True)
    skipped = [(s["id"], s["reason_code"]) for s in report["api_keys"]["skipped"]]
    assert (name, "scoped_privileged_key") in skipped
    assert name not in report["api_keys"]["replaced"]
    assert (await StoredAPIKey.find_one(StoredAPIKey.name == name)).groups == []


# ── A grant to a key the restore does not write ──────────────────────
#
# A grant follows its key's name. Written for a key the restore skips, it
# would go to whichever key holds the name here, or to the next key created
# under it, so it is skipped too (key_not_restored).

from registry.models.grant import NamespaceGrant  # noqa: E402


async def _grant_here(name: str) -> str | None:
    g = await NamespaceGrant.find_one({"namespace": "default", "subject": name})
    return None if g is None else g.permission


@pytest.mark.asyncio
async def test_a_skipped_keys_grant_is_not_written(fresh, auth_headers):  # noqa: F811
    client = fresh
    name = _n()
    assert (await client.post(KEYS, headers=auth_headers, json={"name": name, "namespaces": ["default"]})).status_code == 201
    archive = await _backup(client, auth_headers)
    assert (await client.delete(f"{KEYS}/{name}", headers=auth_headers)).status_code == 200
    next(k for k in archive["api_keys"] if k["name"] == name)["groups"] = [SERVICES_GROUP]

    report = await _restore(client, auth_headers, archive, dry_run=False)
    grants = {s["id"]: s["reason_code"] for s in report["grants"]["skipped"]}
    assert grants[f"default/api_key:{name}"] == "key_not_restored"
    assert await _grant_here(name) is None


@pytest.mark.asyncio
async def test_a_grant_never_goes_to_another_key_of_the_name(fresh, auth_headers):  # noqa: F811
    client = fresh
    name = _n()
    body = {"name": name, "namespaces": ["default"], "grant_permission": "write"}
    assert (await client.post(KEYS, headers=auth_headers, json=body)).status_code == 201
    archive = await _backup(client, auth_headers)
    # Another key of the name here: another plaintext, and no grant yet.
    assert (await client.delete(f"{KEYS}/{name}", headers=auth_headers)).status_code == 200
    assert (await client.post(KEYS, headers=auth_headers, json=body)).status_code == 201
    await NamespaceGrant.find({"namespace": "default", "subject": name}).delete()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert (name, "differs") in [(s["id"], s["reason_code"]) for s in report["api_keys"]["skipped"]]
    grants = {s["id"]: s["reason_code"] for s in report["grants"]["skipped"]}
    assert grants[f"default/api_key:{name}"] == "key_not_restored"
    assert await _grant_here(name) is None


@pytest.mark.asyncio
async def test_the_deployers_key_keeps_the_grants_the_api_gave_it(fresh, auth_headers):  # noqa: F811
    client = fresh
    name = _n()
    body = {"name": name, "namespaces": ["default"], "grant_permission": "write"}
    assert (await client.post(KEYS, headers=auth_headers, json=body)).status_code == 201
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    await doc.set({"managed_by": "wip-deploy", "origin": "file", "grants_managed": False})
    archive = await _backup(client, auth_headers)
    await NamespaceGrant.find({"namespace": "default", "subject": name}).delete()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert (name, "managed_by_deployer") in [
        (s["id"], s["reason_code"]) for s in report["api_keys"]["skipped"]
    ]
    assert f"default/api_key:{name}" in report["grants"]["created"]
    assert await _grant_here(name) == "write"
