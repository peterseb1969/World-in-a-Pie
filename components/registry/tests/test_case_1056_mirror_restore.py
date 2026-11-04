"""Mirror mode for the system restore: the archive becomes the exact state.

A mirror removes every record the archive does not hold (keys as a revoke
takes them, grants, reservations; attachments are deactivated), except
records the install's files manage, the bootstrap grants, user grants the
archive's issuer would not restore, and the caller's own key, whose removal
refuses the plan. The dry run lists the removals and a digest an apply can
pin.
"""

import json
import uuid

import pytest

from registry.main import providers as _app_providers
from registry.models.api_key import StoredAPIKey
from registry.models.attachment import AppAttachment
from registry.models.grant import NamespaceGrant
from registry.models.reservation import RuntimeReservation
from registry.services import attachments as attach_service
from registry.services import key_salt, key_shadowing
from registry.services.system_backup import BOOTSTRAP_GRANTOR, salt_fingerprint
from wip_auth import APIKeyProvider
from wip_auth.key_sync import RUNTIME_SALT_HEADER

KEYS = "/api/registry/api-keys"
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


async def _key(client, headers, name=None, **extra) -> tuple[str, str]:
    name = name or _p("k")
    body = {"name": name, "namespaces": ["default"], **extra}
    r = await client.post(KEYS, headers=headers, json=body)
    assert r.status_code == 201, r.text
    return name, r.json()["plaintext_key"]


async def _grant(client, headers, ns, subject, subject_type="group", permission="read"):
    r = await client.post(f"/api/registry/namespaces/{ns}/grants", headers=headers, json=[
        {"subject": subject, "subject_type": subject_type, "permission": permission},
    ])
    assert r.status_code == 200 and r.json()["results"][0]["status"] == "created", r.text


async def _attach(client, headers, name):
    r = await client.post("/api/registry/attachments", headers=headers, json=[
        {"name": name, "upstream": f"https://{name}.apps.example", "auth_required": True,
         "allowed_groups": []},
    ])
    assert r.json()["results"][0]["status"] == "created", r.text


async def _backup(client, headers) -> dict:
    r = await client.post(BACKUP, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _restore(client, headers, archive, expect_status=200, **params):
    query = {k: str(v).lower() if isinstance(v, bool) else v for k, v in params.items()}
    r = await client.post(RESTORE, json=archive, params=query, headers=headers)
    assert r.status_code == expect_status, r.text
    return r.json()


async def _authenticates(client, plaintext: str) -> bool:
    r = await client.get("/api/registry/my/namespaces", headers={"X-API-Key": plaintext})
    return r.status_code == 200


@pytest.mark.asyncio
async def test_mirror_removes_what_the_archive_does_not_hold(fresh, auth_headers):
    client = fresh
    kept, kept_pt = await _key(client, auth_headers)
    await _grant(client, auth_headers, "vendor1", "analysts")
    await _attach(client, auth_headers, "kept-app")
    archive = await _backup(client, auth_headers)

    # Since the backup: a key with a grant and a reservation, a group grant,
    # an attachment, and a change to the kept key.
    extra, extra_pt = await _key(
        client, auth_headers, namespaces=["default", "resv-x"], reserve=[{"prefix": "resv-x"}],
    )
    await _grant(client, auth_headers, "vendor2", "late-group")
    await _attach(client, auth_headers, "late-app")
    r = await client.patch(f"{KEYS}/{kept}", headers=auth_headers, json={"description": "changed"})
    assert r.status_code == 200, r.text

    dry = await _restore(client, auth_headers, archive, mode="mirror")
    assert dry["mode"] == "mirror" and dry["plan_digest"].startswith("sha256:")
    assert dry["api_keys"]["deleted"] == [extra]
    assert "vendor2/group:late-group" in dry["grants"]["deleted"]
    assert dry["reservations"]["deleted"] == ["resv-x"]
    assert dry["attachments"]["deactivated"] == ["late-app"]
    assert dry["api_keys"]["replaced"] == [kept]  # mirror implies overwrite
    assert await StoredAPIKey.find_one(StoredAPIKey.name == extra) is not None

    report = await _restore(
        client, auth_headers, archive, mode="mirror", dry_run=False, expect=dry["plan_digest"],
    )
    assert report["api_keys"]["deleted"] == [extra]
    assert await StoredAPIKey.find_one(StoredAPIKey.name == extra) is None
    assert not await _authenticates(client, extra_pt)
    assert await _authenticates(client, kept_pt)
    assert await NamespaceGrant.find({"subject": {"$in": [extra, f"apikey:{extra}"]}}).count() == 0
    assert await NamespaceGrant.find_one({"subject": "late-group"}) is None
    assert await NamespaceGrant.find_one({"subject": "analysts"}) is not None
    assert await RuntimeReservation.find_one(RuntimeReservation.prefix == "resv-x") is None
    assert (await AppAttachment.find_one(AppAttachment.name == "late-app")).status == "inactive"
    assert (await AppAttachment.find_one(AppAttachment.name == "kept-app")).status == "active"
    assert (await StoredAPIKey.find_one(StoredAPIKey.name == kept)).description is None

    again = await _restore(client, auth_headers, archive, mode="mirror")
    assert again["api_keys"]["deleted"] == [] and again["grants"]["deleted"] == []


@pytest.mark.asyncio
async def test_mirror_keeps_managed_bootstrap_and_unrestored_user_grants(fresh, auth_headers):
    client = fresh
    archive = await _backup(client, auth_headers)
    archive["source"]["oidc_issuer"] = "https://elsewhere.example/dex"

    managed = _p("managed")
    await StoredAPIKey(name=managed, key_hash="$2b$12$x", managed_by="wip-deploy", origin="file").insert()
    await NamespaceGrant(
        namespace="vendor1", subject="ops", subject_type="group", permission="read",
        granted_by="wip-deploy", managed_by="wip-deploy", origin="file",
    ).insert()
    await NamespaceGrant(
        namespace="vendor1", subject="admins", subject_type="group", permission="admin",
        granted_by=BOOTSTRAP_GRANTOR,
    ).insert()
    await RuntimeReservation(prefix="resv-m", app="cooking", managed_by="wip-deploy").insert()
    await _grant(client, auth_headers, "vendor1", "ann@example.org", subject_type="user")

    dry = await _restore(client, auth_headers, archive, mode="mirror")
    assert dry["api_keys"]["deleted"] == []
    assert dry["grants"]["deleted"] == []
    assert dry["reservations"]["deleted"] == []

    # The same issuer restores user grants, so the mirror owns them too.
    archive["source"]["oidc_issuer"] = (await _backup(client, auth_headers))["source"]["oidc_issuer"]
    dry = await _restore(client, auth_headers, archive, mode="mirror")
    assert dry["grants"]["deleted"] == ["vendor1/user:ann@example.org"]


@pytest.mark.asyncio
async def test_a_plan_that_removes_the_callers_key_is_refused(fresh, auth_headers):
    client = fresh
    archive = await _backup(client, auth_headers)
    admin, admin_pt = await _key(client, auth_headers, groups=["wip-admins"], namespaces=None)
    caller = {"X-API-Key": admin_pt}

    dry = await _restore(client, caller, archive, mode="mirror")
    assert [(r["id"], r["reason_code"]) for r in dry["refused"]] == [(admin, "caller_key")]
    assert admin not in dry["api_keys"]["deleted"]

    body = await _restore(client, caller, archive, expect_status=409, mode="mirror", dry_run=False)
    assert body["error_code"] == "restore_refused"
    assert await StoredAPIKey.find_one(StoredAPIKey.name == admin) is not None


@pytest.mark.asyncio
async def test_an_apply_whose_plan_changed_is_refused(fresh, auth_headers):
    client = fresh
    archive = await _backup(client, auth_headers)
    first, _ = await _key(client, auth_headers)
    dry = await _restore(client, auth_headers, archive, mode="mirror")
    assert dry["api_keys"]["deleted"] == [first]

    later, _ = await _key(client, auth_headers)  # never shown in the dry run
    body = await _restore(
        client, auth_headers, archive, expect_status=409,
        mode="mirror", dry_run=False, expect=dry["plan_digest"],
    )
    assert body["error_code"] == "plan_changed"
    assert await StoredAPIKey.find_one(StoredAPIKey.name == later) is not None
    assert await StoredAPIKey.find_one(StoredAPIKey.name == first) is not None


@pytest.mark.asyncio
async def test_a_grant_to_a_key_the_mirror_removes_is_not_written(fresh, auth_headers):
    client = fresh
    name, _ = await _key(client, auth_headers)
    await _grant(client, auth_headers, "vendor1", name, subject_type="api_key")
    archive = await _backup(client, auth_headers)
    # The archive keeps the grant but not the key (revoked just before the backup).
    archive["api_keys"] = [k for k in archive["api_keys"] if k["name"] != name]

    report = await _restore(client, auth_headers, archive, mode="mirror", dry_run=False)
    gid = f"vendor1/api_key:{name}"
    skipped = {s["id"]: s["reason_code"] for s in report["grants"]["skipped"]}
    assert skipped[gid] == "mirror_removes_key"
    assert gid in report["grants"]["deleted"]
    assert await NamespaceGrant.find_one({"subject": name}) is None


@pytest.mark.asyncio
async def test_a_mirror_adopts_another_salt_on_a_stack_with_keys_of_its_own(
    fresh, auth_headers, provider,
):
    client = fresh
    await StoredAPIKey.delete_all()
    old_salt = await key_salt.ensure()
    provider.set_runtime_salt(old_salt)
    _, plaintext = await _key(client, auth_headers)
    archive = await _backup(client, auth_headers)

    # Another stack: its own salt and its own key.
    for doc in await StoredAPIKey.find_all().to_list():
        provider.remove_key(doc.key_hash)
    await StoredAPIKey.delete_all()
    key_salt.reset_for_tests()
    await key_salt._collection().delete_many({})
    new_salt = await key_salt.ensure()
    provider.set_runtime_salt(new_salt)
    own, own_pt = await _key(client, auth_headers)

    await _restore(client, auth_headers, archive, expect_status=409, dry_run=False)  # upsert refuses
    report = await _restore(client, auth_headers, archive, mode="mirror", dry_run=False)
    assert report["salt_adopted"] is True
    assert report["api_keys"]["deleted"] == [own]
    assert key_salt.current_salt() == old_salt
    assert await _authenticates(client, plaintext)
    assert not await _authenticates(client, own_pt)
    resp = await client.get(f"{KEYS}/sync", headers=auth_headers)
    assert resp.headers[RUNTIME_SALT_HEADER] == old_salt
    assert archive["source"]["salt_fingerprint"] == salt_fingerprint(old_salt)


@pytest.mark.asyncio
async def test_mode_and_the_overwrite_alias(fresh, auth_headers):
    client = fresh
    name, _ = await _key(client, auth_headers)
    archive = await _backup(client, auth_headers)
    r = await client.patch(f"{KEYS}/{name}", headers=auth_headers, json={"description": "x"})
    assert r.status_code == 200

    assert (await _restore(client, auth_headers, archive))["api_keys"]["skipped"]
    assert (await _restore(client, auth_headers, archive, mode="overwrite"))["api_keys"]["replaced"] == [name]
    assert (await _restore(client, auth_headers, archive, overwrite=True))["api_keys"]["replaced"] == [name]
    await _restore(client, auth_headers, archive, expect_status=422, overwrite=True, mode="upsert")
    upsert = await _restore(client, auth_headers, archive, mode="upsert")
    assert upsert["plan_digest"] is None and upsert["api_keys"]["deleted"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["overwrite", "mirror"])
async def test_a_restore_that_would_replace_the_callers_key_is_refused(fresh, auth_headers, mode):
    """An admin key re-created under the same name, restored from an older
    archive that holds the old one: the archive's record would lock the
    caller out."""
    client = fresh
    name, _old = await _key(client, auth_headers, name=_p("ops-admin"), groups=["wip-admins"], namespaces=None)
    archive = await _backup(client, auth_headers)
    r = await client.delete(f"{KEYS}/{name}", headers=auth_headers)
    assert r.status_code == 200, r.text
    _, new_pt = await _key(client, auth_headers, name=name, groups=["wip-admins"], namespaces=None)
    caller = {"X-API-Key": new_pt}

    dry = await _restore(client, caller, archive, mode=mode)
    assert [(x["id"], x["reason_code"]) for x in dry["refused"]] == [(name, "caller_key")]
    body = await _restore(client, caller, archive, expect_status=409, mode=mode, dry_run=False)
    assert body["error_code"] == "restore_refused"
    assert await _authenticates(client, new_pt)

    # The master key is not that key: it may replace it.
    report = await _restore(client, auth_headers, archive, mode=mode, dry_run=False)
    assert name in report["api_keys"]["replaced"]
    assert not await _authenticates(client, new_pt)


@pytest.mark.asyncio
async def test_a_refusal_is_reported_before_a_salt_question(fresh, auth_headers):
    """A mirror dry run from another stack's archive that would remove the
    caller's key reports the refusal, not a salt mismatch."""
    client = fresh
    archive = await _backup(client, auth_headers)
    archive["source"]["salt"] = "other-stack"
    archive["source"]["salt_fingerprint"] = salt_fingerprint("other-stack")
    admin, admin_pt = await _key(client, auth_headers, groups=["wip-admins"], namespaces=None)
    caller = {"X-API-Key": admin_pt}

    dry = await _restore(client, caller, archive, mode="mirror")
    assert [x["id"] for x in dry["refused"]] == [admin]
    assert dry["salt_adopted"] is False
    assert any("refusals below are resolved" in w for w in dry["warnings"])
    body = await _restore(client, caller, archive, expect_status=409, mode="mirror", dry_run=False)
    assert body["error_code"] == "restore_refused"
