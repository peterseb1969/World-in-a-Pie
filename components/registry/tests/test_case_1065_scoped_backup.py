"""A scoped system backup declares what it covers, and its mirror removes only there.

`system-backup?namespaces=a,b&attachments=x` carries the grants and
reservations of those namespaces, the keys whose namespaces all lie in them,
and the named attachments, and declares that scope in `source.scope`. Keys
the scope touches but cannot carry are named in `keys_left_out`. A mirror of
a scoped archive removes only within the scope; the plan digest includes the
scope; a foreign salt still needs a stack with no keys of its own.
"""

import json
import uuid

import pytest
from pydantic import ValidationError

from registry.main import providers as _app_providers
from registry.models.api_key import StoredAPIKey
from registry.models.attachment import AppAttachment
from registry.models.grant import NamespaceGrant
from registry.models.reservation import RuntimeReservation
from registry.services import attachments as attach_service
from registry.services import key_shadowing
from registry.services.system_backup import ArchiveSource, salt_fingerprint
from wip_auth import APIKeyProvider

KEYS = "/api/registry/api-keys"
NS = "/api/registry/namespaces"
BACKUP = "/api/registry/system-backup"
RESTORE = "/api/registry/system-restore"


def _p(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def provider():
    provider = next(p for p in _app_providers if isinstance(p, APIKeyProvider))
    yield provider
    provider.set_runtime_salt(provider.hash_salt)


@pytest.fixture
async def fresh(client, tmp_path, monkeypatch):
    await NamespaceGrant.delete_all()
    key_shadowing.reset_for_tests()
    path = tmp_path / "attach-policy.json"
    path.write_text(json.dumps({"blocked_hosts": [], "reserved_names": []}))
    monkeypatch.setenv(attach_service.ATTACH_POLICY_FILE_ENV, str(path))

    async def _resolve(host):
        import ipaddress
        return {ipaddress.ip_address("203.0.113.20")}

    monkeypatch.setattr(attach_service, "_resolve", _resolve)
    yield client
    key_shadowing.reset_for_tests()


async def _ns(client, headers, prefix):
    assert (await client.put(f"{NS}/{prefix}", json={}, headers=headers)).status_code == 200


async def _key(client, headers, name, namespaces, **extra) -> str:
    r = await client.post(KEYS, headers=headers, json={"name": name, "namespaces": namespaces, **extra})
    assert r.status_code == 201, r.text
    return r.json()["plaintext_key"]


async def _grant(client, headers, ns, subject, subject_type="api_key", permission="read"):
    r = await client.post(f"{NS}/{ns}/grants", headers=headers, json=[
        {"subject": subject, "subject_type": subject_type, "permission": permission},
    ])
    assert r.json()["results"][0]["status"] in ("created", "updated"), r.text


async def _attach(client, headers, name):
    r = await client.post("/api/registry/attachments", headers=headers, json=[
        {"name": name, "upstream": f"https://{name}.apps.example", "auth_required": True,
         "allowed_groups": []},
    ])
    assert r.json()["results"][0]["status"] == "created", r.text


async def _backup(client, headers, **params) -> dict:
    r = await client.post(BACKUP, params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _restore(client, headers, archive, **params) -> dict:
    q = {k: str(v).lower() for k, v in params.items()}
    r = await client.post(RESTORE, json=archive, params=q, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _stack(client, headers) -> dict:
    """Two app namespaces a and b, and z outside the scope. Keys: in_a (a
    only), in_ab (a and b), spans (a and z), unscoped (namespaces null),
    outside (z only). Attachments app-a and app-z."""
    a, b, z = _p("a"), _p("b"), _p("z")
    for prefix in (a, b, z):
        await _ns(client, headers, prefix)
    names = {tag: _p(tag) for tag in ("in-a", "in-ab", "spans", "unscoped", "outside")}
    await _key(client, headers, names["in-a"], [a], grant_permission="write")
    await _key(client, headers, names["in-ab"], [a, b], grant_permission="read")
    await _key(client, headers, names["spans"], [a, z], grant_permission="read")
    await _key(client, headers, names["unscoped"], None, groups=["wip-services"])
    await _key(client, headers, names["outside"], [z], grant_permission="read")
    await _grant(client, headers, a, names["unscoped"])
    await _grant(client, headers, z, "z-readers", "group")
    app_a, app_z = _p("app-a"), _p("app-z")
    await _attach(client, headers, app_a)
    await _attach(client, headers, app_z)
    return {"a": a, "b": b, "z": z, "keys": names, "app_a": app_a, "app_z": app_z}


@pytest.mark.asyncio
async def test_a_scoped_backup_carries_only_its_scope(fresh, auth_headers):
    client = fresh
    s = await _stack(client, auth_headers)
    k = s["keys"]
    archive = await _backup(client, auth_headers, namespaces=f"{s['a']},{s['b']}", attachments=s["app_a"])

    scope = archive["source"]["scope"]
    assert scope["namespaces"] == sorted([s["a"], s["b"]])
    assert scope["attachments"] == [s["app_a"]]
    assert {x["name"] for x in archive["api_keys"]} == {k["in-a"], k["in-ab"]}
    assert scope["keys_left_out"] == sorted([k["spans"], k["unscoped"]])
    assert {g["namespace"] for g in archive["grants"]} <= {s["a"], s["b"]}
    assert {x["name"] for x in archive["attachments"]} == {s["app_a"]}

    whole = await _backup(client, auth_headers)
    assert whole["source"].get("scope") is None
    assert {k["outside"], k["spans"]} <= {x["name"] for x in whole["api_keys"]}


@pytest.mark.asyncio
async def test_a_scoped_mirror_removes_only_within_its_scope(fresh, auth_headers):
    client = fresh
    s = await _stack(client, auth_headers)
    k = s["keys"]
    archive = await _backup(client, auth_headers, namespaces=f"{s['a']},{s['b']}")
    # The archive as it would be if in-a, the spanning key's grant on a and
    # nothing else had been there; meanwhile the stack gained records on all sides.
    archive["api_keys"] = [x for x in archive["api_keys"] if x["name"] != k["in-a"]]
    await _grant(client, auth_headers, s["a"], "late-a", "group")
    await _grant(client, auth_headers, s["z"], "late-z", "group")
    await _key(client, auth_headers, _p("late-outside"), [s["z"]])

    report = await _restore(client, auth_headers, archive, mode="mirror")
    assert report["api_keys"]["deleted"] == [k["in-a"]]
    deleted_grants = set(report["grants"]["deleted"])
    assert f"{s['a']}/group:late-a" in deleted_grants
    assert all(g.split("/")[0] in (s["a"], s["b"]) for g in deleted_grants), deleted_grants
    assert report["attachments"]["deactivated"] == []  # none named, none touched
    assert any("scoped archive" in w for w in report["warnings"])

    applied = await _restore(client, auth_headers, archive, mode="mirror", dry_run=False,
                             expect=report["plan_digest"])
    assert applied["api_keys"]["deleted"] == [k["in-a"]]
    stored = {x.name for x in await StoredAPIKey.find_all().to_list()}
    assert k["in-a"] not in stored
    assert {k["spans"], k["unscoped"], k["outside"], k["in-ab"]} <= stored
    assert await NamespaceGrant.find_one({"namespace": s["z"], "subject": "late-z"}) is not None
    assert await NamespaceGrant.find_one({"namespace": s["a"], "subject": "late-a"}) is None
    active = {a.name for a in await AppAttachment.find({"status": "active"}).to_list()}
    assert {s["app_a"], s["app_z"]} <= active


@pytest.mark.asyncio
async def test_named_attachments_are_the_only_ones_a_scoped_mirror_deactivates(fresh, auth_headers):
    client = fresh
    s = await _stack(client, auth_headers)
    archive = await _backup(client, auth_headers, namespaces=s["a"], attachments=s["app_a"])
    archive["attachments"] = []
    report = await _restore(client, auth_headers, archive, mode="mirror")
    assert report["attachments"]["deactivated"] == [s["app_a"]]


@pytest.mark.asyncio
async def test_reservations_follow_the_scope(fresh, auth_headers):
    client = fresh
    a, z = _p("a"), _p("z")
    await _key(client, auth_headers, _p("own-a"), [a], reserve=[{"prefix": a}])
    await _key(client, auth_headers, _p("own-z"), [z], reserve=[{"prefix": z}])
    archive = await _backup(client, auth_headers, namespaces=a)
    assert [r["prefix"] for r in archive["reservations"]] == [a]
    archive["reservations"] = []
    archive["api_keys"] = []
    report = await _restore(client, auth_headers, archive, mode="mirror")
    assert report["reservations"]["deleted"] == [a]
    assert await RuntimeReservation.find_one(RuntimeReservation.prefix == z) is not None


@pytest.mark.asyncio
async def test_grants_to_keys_the_archive_does_not_carry_are_carried_with_a_warning(fresh, auth_headers):
    client = fresh
    s = await _stack(client, auth_headers)
    k = s["keys"]
    archive = await _backup(client, auth_headers, namespaces=s["a"])
    spans_grant = f"{s['a']}/api_key:{k['spans']}"
    assert spans_grant in {f"{g['namespace']}/{g['subject_type']}:{g['subject']}" for g in archive["grants"]}
    report = await _restore(client, auth_headers, archive)
    warning = next(w for w in report["warnings"] if "keys the archive does not carry" in w)
    assert spans_grant in warning and f"{s['a']}/api_key:{k['unscoped']}" in warning


@pytest.mark.asyncio
async def test_the_plan_digest_includes_the_scope(fresh, auth_headers):
    client = fresh
    s = await _stack(client, auth_headers)
    one = await _backup(client, auth_headers, namespaces=s["b"])
    two = await _backup(client, auth_headers, namespaces=f"{s['b']},{_p('empty')}")
    d1 = (await _restore(client, auth_headers, one, mode="mirror"))["plan_digest"]
    d2 = (await _restore(client, auth_headers, two, mode="mirror"))["plan_digest"]
    assert d1 != d2
    # An apply under the other scope refuses the first one's digest.
    r = await client.post(RESTORE, json=two, headers=auth_headers,
                          params={"mode": "mirror", "dry_run": "false", "expect": d1})
    assert r.status_code == 409 and r.json()["error_code"] == "plan_changed"


@pytest.mark.asyncio
async def test_a_foreign_salt_needs_a_stack_without_keys_outside_the_scope(fresh, auth_headers, provider):
    client = fresh
    s = await _stack(client, auth_headers)
    archive = await _backup(client, auth_headers, namespaces=s["a"])
    archive["source"]["salt"] = "another-stack-salt"
    archive["source"]["salt_fingerprint"] = salt_fingerprint("another-stack-salt")
    r = await client.post(RESTORE, json=archive, headers=auth_headers, params={"mode": "mirror"})
    assert r.status_code == 409 and r.json()["error_code"] == "salt_mismatch"

    # A stack with no keys of its own adopts it, scoped or not.
    for doc in await StoredAPIKey.find({"managed_by": None}).to_list():
        provider.remove_key(doc.key_hash)
    await StoredAPIKey.find({"managed_by": None}).delete()
    report = await _restore(client, auth_headers, archive, mode="mirror")
    assert report["salt_adopted"] is True


def test_an_archive_source_refuses_fields_it_does_not_know():
    """Why the format stays version 1: a Registry whose ArchiveSource lacks
    `scope` refuses a scoped archive instead of reading it as a whole-stack
    one. The same strictness holds today for any field it does not know."""
    base = {"salt_fingerprint": salt_fingerprint("x")}
    assert ArchiveSource(**base, scope={"namespaces": ["a"]}).scope.namespaces == ["a"]
    with pytest.raises(ValidationError):
        ArchiveSource(**base, scope_v2={"namespaces": ["a"]})


@pytest.mark.asyncio
async def test_a_key_the_source_left_out_survives_even_when_its_copy_here_fits_the_scope(fresh, auth_headers):
    """Review CASE-1065#3: the scope of a key is the archive's word, not this
    stack's copy. A key that spans the scope on the source is left out; here
    it holds only in-scope namespaces (narrowed, or created with fewer). The
    mirror must not remove it, nor its grants inside or outside the scope."""
    client = fresh
    s = await _stack(client, auth_headers)
    spans = s["keys"]["spans"]
    archive = await _backup(client, auth_headers, namespaces=s["a"])
    assert spans in archive["source"]["scope"]["keys_left_out"]
    # Here the key's copy fits the scope; its grant on z stays as a record.
    await StoredAPIKey.find_one(StoredAPIKey.name == spans).update({"$set": {"namespaces": [s["a"]]}})

    report = await _restore(client, auth_headers, archive, mode="mirror")
    assert spans not in report["api_keys"]["deleted"]
    applied = await _restore(client, auth_headers, archive, mode="mirror", dry_run=False,
                             expect=report["plan_digest"])
    assert spans not in applied["api_keys"]["deleted"]
    assert await StoredAPIKey.find_one(StoredAPIKey.name == spans) is not None
    assert await NamespaceGrant.find_one({"namespace": s["z"], "subject": spans}) is not None
    assert await NamespaceGrant.find_one({"namespace": s["a"], "subject": spans}) is not None


@pytest.mark.asyncio
async def test_reservations_owned_by_an_uncarried_key_are_named(fresh, auth_headers):
    client = fresh
    a, z = _p("a"), _p("z")
    owner = _p("owner")
    await _key(client, auth_headers, owner, [a, z], reserve=[{"prefix": a}])
    archive = await _backup(client, auth_headers, namespaces=a)
    assert owner in archive["source"]["scope"]["keys_left_out"]
    assert [r["prefix"] for r in archive["reservations"]] == [a]
    report = await _restore(client, auth_headers, archive)
    warning = next(w for w in report["warnings"] if "Reservations owned by keys" in w)
    assert f"{a} (owner {owner})" in warning
