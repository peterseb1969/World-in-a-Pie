"""The Registry imports the install's key file and reservations file.

One key model: a key, its declared grants and a reservation the deployer
renders become Registry records marked managed_by wip-deploy. A pass creates
what the files declare, corrects what differs, and removes managed records
the files no longer declare; a file it cannot trust changes nothing. A
collision is won by the managed record, loudly. The API refuses every write
to a managed record (409, error_code managed_by_deployer). While the Registry
also loads the key file as config keys, an imported key is dormant: one
plaintext, one record, so it stays out of key sync.
"""

import json
import uuid

import pytest

from registry.main import providers as _app_providers
from registry.models.api_key import StoredAPIKey
from registry.models.grant import NamespaceGrant
from registry.models.reservation import RuntimeReservation
from registry.services import key_import, key_shadowing
from registry.services.key_import import CANARY_KEY_NAME, KEY_FILE_ENV
from registry.services.reservations import RESERVATIONS_FILE_ENV
from wip_auth import APIKeyProvider, APIKeyRecord
from wip_auth.providers import api_key as api_key_module
from wip_auth.providers.api_key import api_key_prehash, hash_api_key

KEYS = "/api/registry/api-keys"
NS = "/api/registry/namespaces"
RES = "/api/registry/reservations"


def _p(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


def _provider() -> APIKeyProvider:
    return next(p for p in _app_providers if isinstance(p, APIKeyProvider))


@pytest.fixture
def provider():
    provider = _provider()
    names = provider.config_key_names()
    original = [k for k in provider.iter_keys() if k.name in names]
    key_shadowing.reset_for_tests()
    yield provider
    provider.reload_config_keys(original)
    key_shadowing.reset_for_tests()


CANARY = {"name": CANARY_KEY_NAME, "key": "canary-plaintext-0001", "namespaces": [], "grants": {}}


def _app_entry(name: str, plaintext: str, namespaces: list[str], grants: dict | None) -> dict:
    entry = {"name": name, "key": plaintext, "owner": f"app:{name}", "groups": [],
             "namespaces": namespaces}
    if grants is not None:
        entry["grants"] = grants
    return entry


def _key_file(monkeypatch, tmp_path, *entries: dict, canary: bool = True, raw: str | None = None):
    path = tmp_path / "api-keys.json"
    keys = [*([CANARY] if canary else []), *entries]
    path.write_text(raw if raw is not None else json.dumps({"keys": keys}))
    monkeypatch.setenv(KEY_FILE_ENV, str(path))
    return path


def _res_file(monkeypatch, tmp_path, *reservations: dict, raw: str | None = None):
    path = tmp_path / "namespace-reservations.json"
    path.write_text(raw if raw is not None else json.dumps({"reservations": list(reservations)}))
    monkeypatch.setenv(RESERVATIONS_FILE_ENV, str(path))
    return path


async def _grants_of(name: str) -> dict[str, tuple[str, str | None]]:
    grants = await NamespaceGrant.find({"subject": name, "subject_type": "api_key"}).to_list()
    return {g.namespace: (g.permission, g.managed_by) for g in grants}


# --- import, change, removal ---------------------------------------------


@pytest.mark.asyncio
async def test_a_pass_imports_keys_grants_and_reservations(client, monkeypatch, tmp_path):
    name, ns = _p("app-k"), _p("ns")
    _key_file(monkeypatch, tmp_path, _app_entry(name, "pt-" + name, [ns, "wip"], {ns: "admin"}))
    _res_file(monkeypatch, tmp_path, {"prefix": ns, "app": "cooking", "settings": {"deletion_mode": "full"}})

    report = await key_import.run_pass()
    assert set(report.keys_created) == {name, CANARY_KEY_NAME}

    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    assert (doc.managed_by, doc.origin, doc.grants_managed) == ("wip-deploy", "file", True)
    assert doc.namespaces == [ns, "wip"]
    assert api_key_module.verify_api_key("pt-" + name, doc.key_hash)
    # Namespaces give read, grants their level: wip-auth's config-key rule.
    assert await _grants_of(name) == {ns: ("admin", "wip-deploy"), "wip": ("read", "wip-deploy")}

    res = await RuntimeReservation.find_one(RuntimeReservation.prefix == ns)
    assert (res.managed_by, res.app, res.settings) == ("wip-deploy", "cooking", {"deletion_mode": "full"})


@pytest.mark.asyncio
async def test_an_unchanged_pass_costs_no_bcrypt_and_a_changed_key_is_rehashed(
    client, monkeypatch, tmp_path,
):
    name = _p("app-k")
    _key_file(monkeypatch, tmp_path, _app_entry(name, "first-" + name, [], {}))
    await key_import.run_pass()
    first = (await StoredAPIKey.find_one(StoredAPIKey.name == name)).key_hash

    calls = []
    real_hash, real_match = key_import.hash_api_key, key_import.prehash_matches
    monkeypatch.setattr(key_import, "hash_api_key", lambda *a: calls.append("hash") or real_hash(*a))
    monkeypatch.setattr(key_import, "prehash_matches", lambda *a: calls.append("check") or real_match(*a))

    report = await key_import.run_pass()
    assert calls == []
    assert report.keys_created == report.keys_updated == []
    assert (await StoredAPIKey.find_one(StoredAPIKey.name == name)).key_hash == first

    _key_file(monkeypatch, tmp_path, _app_entry(name, "second-" + name, [], {}))
    report = await key_import.run_pass()
    assert report.keys_updated == [name]
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    assert doc.key_hash != first
    assert api_key_module.verify_api_key("second-" + name, doc.key_hash)


@pytest.mark.asyncio
async def test_after_a_restart_an_unchanged_key_costs_one_check_and_no_rehash(
    client, monkeypatch, tmp_path,
):
    name = _p("app-k")
    _key_file(monkeypatch, tmp_path, _app_entry(name, "pt-" + name, [], {}))
    await key_import.run_pass()
    first = (await StoredAPIKey.find_one(StoredAPIKey.name == name)).key_hash
    key_import.reset_for_tests()  # what a restart forgets

    await key_import.run_pass()
    assert (await StoredAPIKey.find_one(StoredAPIKey.name == name)).key_hash == first


@pytest.mark.asyncio
async def test_a_key_the_file_drops_goes_with_its_grants(client, monkeypatch, tmp_path):
    keep, drop, ns = _p("app-keep"), _p("app-drop"), _p("ns")
    _key_file(monkeypatch, tmp_path,
              _app_entry(keep, "pt-" + keep, [ns], {}), _app_entry(drop, "pt-" + drop, [ns], {ns: "write"}))
    _res_file(monkeypatch, tmp_path, {"prefix": ns, "app": "cooking"})
    await key_import.run_pass()
    assert await _grants_of(drop) == {ns: ("write", "wip-deploy")}

    _key_file(monkeypatch, tmp_path, _app_entry(keep, "pt-" + keep, [ns], {}))
    _res_file(monkeypatch, tmp_path)
    report = await key_import.run_pass()
    assert report.keys_removed == [drop]
    assert await StoredAPIKey.find_one(StoredAPIKey.name == drop) is None
    assert await _grants_of(drop) == {}
    assert await RuntimeReservation.find_one(RuntimeReservation.prefix == ns) is None
    assert await StoredAPIKey.find_one(StoredAPIKey.name == keep) is not None


@pytest.mark.asyncio
async def test_records_the_import_did_not_make_are_never_removed(client, auth_headers, monkeypatch, tmp_path):
    console = _p("console")
    assert (await client.post(KEYS, json={"name": console, "namespaces": ["default"]},
                              headers=auth_headers)).status_code == 201
    await StoredAPIKey(name=_p("restored"), key_hash=hash_api_key("x", "wip_auth_salt"),
                       origin="restored").insert()
    _key_file(monkeypatch, tmp_path)
    report = await key_import.run_pass()
    assert report.keys_removed == []
    assert await StoredAPIKey.find({"origin": {"$in": ["console", "restored"]}}).count() == 2


# --- bad reads ------------------------------------------------------------


@pytest.mark.parametrize("broken", ["no-canary", "unparseable", "missing", "not-a-list"])
@pytest.mark.asyncio
async def test_a_key_file_it_cannot_trust_changes_nothing(client, monkeypatch, tmp_path, broken):
    name = _p("app-k")
    path = _key_file(monkeypatch, tmp_path, _app_entry(name, "pt-" + name, [], {}))
    await key_import.run_pass()

    if broken == "no-canary":
        _key_file(monkeypatch, tmp_path, canary=False)
    elif broken == "unparseable":
        path.write_text("{not json")
    elif broken == "missing":
        path.unlink()
    else:
        _key_file(monkeypatch, tmp_path, raw=json.dumps({"keys": "nope"}))
    report = await key_import.run_pass()
    assert report.skipped and report.keys_removed == []
    assert await StoredAPIKey.find_one(StoredAPIKey.name == name) is not None


@pytest.mark.asyncio
async def test_an_unusable_reservations_file_removes_nothing_and_answers_503(
    client, auth_headers, monkeypatch, tmp_path,
):
    ns = _p("ns")
    path = _res_file(monkeypatch, tmp_path, {"prefix": ns, "app": "cooking"})
    await key_import.run_pass()
    path.write_text("{broken")

    resp = await client.put(f"{NS}/{ns}", json={}, headers=auth_headers)
    assert resp.status_code in (200, 503)  # an admin upsert need not consult it
    listed = await client.get(RES, headers=auth_headers)
    assert listed.status_code == 503
    assert await RuntimeReservation.find_one(RuntimeReservation.prefix == ns) is not None


@pytest.mark.asyncio
async def test_a_changed_reservations_file_is_imported_before_a_lookup(client, auth_headers, monkeypatch, tmp_path):
    ns = _p("ns")
    _res_file(monkeypatch, tmp_path, {"prefix": ns, "app": "cooking"})
    # No pass ran: the listing brings the import up to date first.
    listed = [r for r in (await client.get(RES, headers=auth_headers)).json() if r["prefix"] == ns]
    assert [(r["source"], r["managed_by"]) for r in listed] == [("file", "wip-deploy")]


# --- collisions: the managed record wins, loudly --------------------------


@pytest.mark.asyncio
async def test_a_console_key_with_a_file_keys_name_is_taken_over(client, auth_headers, monkeypatch, tmp_path, caplog):
    name = _p("app-k")
    created = await client.post(KEYS, json={"name": name, "namespaces": ["default"]}, headers=auth_headers)
    old_plaintext = created.json()["plaintext_key"]
    _key_file(monkeypatch, tmp_path, _app_entry(name, "file-" + name, ["default"], {}))

    with caplog.at_level("CRITICAL", logger="registry.key_import"):
        report = await key_import.run_pass()
    assert f"api_key:{name}" in report.taken_over
    assert any(name in r.message for r in caplog.records if r.levelname == "CRITICAL")
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    assert (doc.managed_by, doc.origin) == ("wip-deploy", "file")
    assert not api_key_module.verify_api_key(old_plaintext, doc.key_hash)
    assert api_key_module.verify_api_key("file-" + name, doc.key_hash)


@pytest.mark.asyncio
async def test_a_console_grant_on_a_declared_namespace_is_taken_over_and_others_removed(
    client, monkeypatch, tmp_path,
):
    name, ns, other = _p("app-k"), _p("ns"), _p("other")
    await NamespaceGrant(namespace=ns, subject=name, subject_type="api_key",
                         permission="read", granted_by="admin").create()
    await NamespaceGrant(namespace=other, subject=name, subject_type="api_key",
                         permission="write", granted_by="admin").create()
    _key_file(monkeypatch, tmp_path, _app_entry(name, "pt-" + name, [ns], {ns: "admin"}))
    report = await key_import.run_pass()
    assert f"grant:{ns}:{name}" in report.taken_over
    # The file owns this key's grants: the API-made one elsewhere goes.
    assert await _grants_of(name) == {ns: ("admin", "wip-deploy")}


@pytest.mark.asyncio
async def test_a_key_declaring_no_grants_keeps_its_api_grants(client, auth_headers, monkeypatch, tmp_path):
    name = _p("spec-k")
    await NamespaceGrant(namespace="default", subject=name, subject_type="api_key",
                         permission="write", granted_by="admin").create()
    _key_file(monkeypatch, tmp_path, _app_entry(name, "pt-" + name, ["default"], None))
    await key_import.run_pass()
    assert await _grants_of(name) == {"default": ("write", None)}
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    assert doc.grants_managed is False
    # ... and the API may still change them.
    resp = await client.post(f"{NS}/default/grants", headers=auth_headers,
                             json=[{"subject": name, "subject_type": "api_key", "permission": "read"}])
    assert resp.json()["results"][0]["status"] == "updated"


@pytest.mark.asyncio
async def test_a_console_key_with_a_file_keys_plaintext_is_shadowed(client, auth_headers, monkeypatch, tmp_path, provider):
    created = await client.post(KEYS, json={"name": _p("pasted"), "namespaces": ["default"]}, headers=auth_headers)
    pasted = created.json()
    name = _p("app-k")
    # The file key's plaintext is the Console key's (an operator pasted it).
    _key_file(monkeypatch, tmp_path, _app_entry(name, pasted["plaintext_key"], [], {}))
    await key_import.run_pass(provider)
    shadows = await key_shadowing.reconcile(provider)
    assert shadows == {pasted["name"]: name}


# --- dormant while the Registry loads the same file as config keys -------


@pytest.mark.asyncio
async def test_an_import_is_dormant_while_its_name_is_a_loaded_config_key(
    client, auth_headers, monkeypatch, tmp_path, provider,
):
    name = _p("app-k")
    plaintext = "pt-" + name
    _key_file(monkeypatch, tmp_path, _app_entry(name, plaintext, ["default"], {"default": "write"}))
    record = APIKeyRecord(name=name, key_hash=hash_api_key(plaintext, provider.hash_salt),
                          namespaces=["default"], grants={"default": "write"})
    record._plaintext_prehash = api_key_prehash(plaintext, provider.hash_salt)
    names = provider.config_key_names()
    provider.reload_config_keys([*(k for k in provider.iter_keys() if k.name in names), record])

    await key_import.run_pass(provider)
    synced = {r["name"] for r in (await client.get(f"{KEYS}/sync", headers=auth_headers)).json()}
    assert name not in synced
    listed = [k for k in (await client.get(f"{KEYS}?page_size=1000", headers=auth_headers)).json()["items"]
              if k["name"] == name]
    assert [(k["source"], k["managed_by"]) for k in listed] == [("config", "wip-deploy")]
    assert provider.lookup(plaintext).name == name

    # Once the Registry no longer loads it as a config key, the record is the key.
    provider.reload_config_keys([k for k in provider.iter_keys() if k.name in names])
    await key_import.run_pass(provider)
    synced = {r["name"] for r in (await client.get(f"{KEYS}/sync", headers=auth_headers)).json()}
    assert name in synced


# --- the API refuses writes to managed records ----------------------------


@pytest.mark.asyncio
async def test_every_api_write_to_a_managed_key_is_refused(client, auth_headers, monkeypatch, tmp_path):
    name, ns = _p("app-k"), _p("ns")
    _key_file(monkeypatch, tmp_path, _app_entry(name, "pt-" + name, ["default"], {"default": "write"}))
    _res_file(monkeypatch, tmp_path, {"prefix": ns, "app": "cooking"})
    await key_import.run_pass()

    attempts = [
        client.post(KEYS, json={"name": name, "namespaces": ["default"]}, headers=auth_headers),
        client.patch(f"{KEYS}/{name}", json={"description": "x"}, headers=auth_headers),
        client.put(f"{KEYS}/{name}/namespaces/{_p('more')}", json={}, headers=auth_headers),
        client.delete(f"{KEYS}/{name}/namespaces/default", headers=auth_headers),
        client.delete(f"{KEYS}/{name}", headers=auth_headers),
        client.delete(f"{RES}/{ns}", headers=auth_headers),
    ]
    for attempt in attempts:
        resp = await attempt
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "managed_by_deployer"
        assert isinstance(resp.json()["detail"], str)

    # Reserving a prefix the install's file reserves, through another key.
    other = await client.post(KEYS, headers=auth_headers, json={
        "name": _p("ext"), "namespaces": [ns], "reserve": [{"prefix": ns, "settings": {}}]})
    assert other.status_code == 409 and other.json()["error_code"] == "managed_by_deployer"

    for method in ("post", "delete"):
        body = [{"subject": name, "subject_type": "api_key", "permission": "admin"}]
        if method == "delete":
            body = [{"subject": name, "subject_type": "api_key"}]
        resp = await client.request(method.upper(), f"{NS}/default/grants", headers=auth_headers, json=body)
        item = resp.json()["results"][0]
        assert (item["status"], item["error_code"]) == ("error", "managed_by_deployer")
    assert await _grants_of(name) == {"default": ("write", "wip-deploy")}

    got = (await client.get(f"{NS}/default/grants", headers=auth_headers)).json()
    items = got if isinstance(got, list) else got["items"]
    assert any(g["subject"] == name and g["managed_by"] == "wip-deploy" for g in items)
