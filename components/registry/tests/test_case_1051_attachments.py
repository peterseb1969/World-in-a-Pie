"""App attachments: an app put under the umbrella through the API (CASE-1051).

An attachment is one record per app, keyed by name, owning /apps/<name>.
Writes are bulk upserts that need platform admin; an attachment is never
hard-deleted, only deactivated and revived. Names the install's own apps
route under are refused, and so is an upstream that resolves to one of the
install's hosts or to a loopback, link-local, unspecified or multicast
address (the attach-time check; the router's edge checks again).
"""

import ipaddress
import json

import pytest

from registry.models.attachment import AppAttachment
from registry.services import attachments as attach_service

URL = "/api/registry/attachments"

# Fake DNS: name → addresses. Anything else does not resolve.
DNS = {
    "aa.apps.example": {"203.0.113.10"},
    "wip-registry": {"10.89.0.5"},
    "registry-alias.example": {"10.89.0.5"},
    "sneaky.example": {"169.254.169.254"},
    "home.example": {"127.0.0.1"},
}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    async def _resolve(host: str):
        try:
            return {ipaddress.ip_address(host)}
        except ValueError:
            return {ipaddress.ip_address(a) for a in DNS.get(host, set())}

    monkeypatch.setattr(attach_service, "_resolve", _resolve)


@pytest.fixture
def policy(tmp_path, monkeypatch):
    path = tmp_path / "attach-policy.json"
    path.write_text(json.dumps({"blocked_hosts": ["wip-registry", "registry"], "reserved_names": ["kb", "rc"]}))
    monkeypatch.setenv(attach_service.ATTACH_POLICY_FILE_ENV, str(path))
    return path


def _item(name="aa", upstream="https://aa.apps.example", **extra):
    return {"name": name, "upstream": upstream, "auth_required": True, "allowed_groups": [], **extra}


async def _post(client, headers, *items):
    r = await client.post(URL, json=list(items), headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_attach_creates_then_is_unchanged(client, auth_headers, policy):
    body = await _post(client, auth_headers, _item(display_name="AuthorAssist"))
    res = body["results"][0]
    assert (res["status"], res["name"], res["path"]) == ("created", "aa", "/apps/aa")
    assert body["succeeded"] == 1

    again = await _post(client, auth_headers, _item(display_name="AuthorAssist"))
    assert again["results"][0]["status"] == "unchanged"

    got = (await client.get(f"{URL}/aa", headers=auth_headers)).json()
    assert got["upstream"] == "https://aa.apps.example"
    assert got["path"] == "/apps/aa"
    assert got["status"] == "active"
    assert got["managed_by"] is None
    assert got["auth_required"] is True


async def test_update_changes_fields(client, auth_headers, policy):
    await _post(client, auth_headers, _item())
    body = await _post(client, auth_headers, _item(allowed_groups=["aa-users"], strip_prefix=True))
    assert body["results"][0]["status"] == "updated"
    doc = await AppAttachment.find_one(AppAttachment.name == "aa")
    assert doc.allowed_groups == ["aa-users"] and doc.strip_prefix is True


async def test_deactivate_and_revive(client, auth_headers, policy):
    await _post(client, auth_headers, _item())
    r = await client.post(f"{URL}/deactivate", json=[{"name": "aa"}, {"name": "nope"}], headers=auth_headers)
    results = r.json()["results"]
    assert results[0]["status"] == "deactivated"
    assert (results[1]["status"], results[1]["error_code"]) == ("error", "not_found")
    assert await AppAttachment.find_one(AppAttachment.name == "aa") is not None  # never deleted

    active = (await client.get(URL, params={"status": "active"}, headers=auth_headers)).json()
    assert active["items"] == [] and active["total"] == 0

    revived = await _post(client, auth_headers, _item())
    assert revived["results"][0]["status"] == "updated"
    assert (await client.get(f"{URL}/aa", headers=auth_headers)).json()["status"] == "active"


async def test_a_name_the_install_routes_is_refused(client, auth_headers, policy):
    body = await _post(client, auth_headers, _item(name="kb"))
    assert body["results"][0]["error_code"] == "name_collision"
    assert await AppAttachment.find_one(AppAttachment.name == "kb") is None


@pytest.mark.parametrize(
    ("upstream", "why"),
    [
        ("http://wip-registry:8001", "install's own hosts"),
        ("http://registry:8001", "install's own hosts"),
        ("http://10.89.0.5:8001", "install's own host"),
        ("http://registry-alias.example", "install's own host"),
        ("http://169.254.169.254", "link-local"),
        ("http://sneaky.example", "link-local"),
        ("http://127.0.0.1:2019", "loopback"),
        ("http://home.example", "loopback"),
        ("http://[::1]:8080", "loopback"),
        ("http://0.0.0.0", "unspecified"),
        ("http://[fd00:ec2::254]", "cloud metadata"),
        ("http://100.100.100.200", "cloud metadata"),
        ("http://[::ffff:127.0.0.1]", "loopback"),
        ("http://[::ffff:169.254.169.254]", "link-local"),
        ("http://[64:ff9b::7f00:1]", "loopback"),
        ("http://[64:ff9b::a9fe:a9fe]", "link-local"),
        ("http://[64:ff9b:1::6464:64c8]", "cloud metadata"),
    ],
)
async def test_blocked_upstreams_are_refused(client, auth_headers, policy, upstream, why):
    body = await _post(client, auth_headers, _item(upstream=upstream))
    res = body["results"][0]
    assert (res["status"], res["error_code"]) == ("error", "forbidden")
    assert res["details"]["rule"] == "upstream_blocked"
    assert why in res["error"]


@pytest.mark.parametrize(
    "upstream",
    [
        "ftp://aa.apps.example",
        "https://user:pw@aa.apps.example",
        "https://aa.apps.example/?x=1",
        "aa.apps.example",
        "https://aa.apps.example/some/base/path",
    ],
)
async def test_malformed_upstreams_are_refused(client, auth_headers, policy, upstream):
    res = (await _post(client, auth_headers, _item(upstream=upstream)))["results"][0]
    assert (res["error_code"], res["details"]["rule"]) == ("validation_failed", "upstream_invalid")


async def test_an_unresolvable_host_is_accepted_with_a_warning(client, auth_headers, policy):
    res = (await _post(client, auth_headers, _item(upstream="http://aa.other-site.internal:3000")))["results"][0]
    assert res["status"] == "created"
    assert "checked again where the router's app edge resolves it" in res["warnings"][0]


async def test_items_fail_independently(client, auth_headers, policy):
    body = await _post(
        client, auth_headers,
        _item(name="aa"), _item(name="rc"), _item(name="aa", upstream="https://aa.apps.example/x"),
    )
    statuses = [r["status"] for r in body["results"]]
    assert statuses == ["created", "error", "error"]
    assert body["results"][2]["details"]["rule"] == "duplicate_name"
    assert (body["succeeded"], body["failed"]) == (1, 2)


@pytest.mark.parametrize("damage", ["{not json", "", None])
async def test_a_broken_missing_or_empty_policy_file_refuses(client, auth_headers, policy, damage):
    if damage is None:
        policy.unlink()
    else:
        policy.write_text(damage)
    res = (await _post(client, auth_headers, _item()))["results"][0]
    assert (res["error_code"], res["details"]["rule"]) == ("validation_failed", "policy_unavailable")


async def test_without_a_policy_file_only_address_classes_are_refused(client, auth_headers, monkeypatch):
    monkeypatch.delenv(attach_service.ATTACH_POLICY_FILE_ENV, raising=False)
    ok = (await _post(client, auth_headers, _item(name="kb")))["results"][0]
    assert ok["status"] == "created"
    bad = (await _post(client, auth_headers, _item(name="x1", upstream="http://127.0.0.1")))["results"][0]
    assert bad["error_code"] == "forbidden"


@pytest.mark.parametrize(
    "item",
    [
        # The fields that decide who may reach the app are required: a
        # partial write must fail, never fall back to "any logged-in user".
        {"name": "aa", "upstream": "https://aa.apps.example"},
        {"name": "aa", "upstream": "https://aa.apps.example", "auth_required": True},
        {"name": "aa", "upstream": "https://aa.apps.example", "allowed_groups": []},
        {"name": "AA", "upstream": "https://aa.apps.example"},
        {"name": "a", "upstream": "https://aa.apps.example"},
        {"name": "aa", "upstream": "https://aa.apps.example", "allowed_groups": ["x"], "auth_required": False},
        {"name": "aa", "upstream": "https://aa.apps.example", "path": "/api/registry"},
        {"name": "aa", "upstream": "https://aa.apps.example", "upstream_tls": {"ca_pem": "/etc/ca.pem"}},
    ],
)
async def test_request_validation(client, auth_headers, policy, item):
    r = await client.post(URL, json=[item], headers=auth_headers)
    assert r.status_code == 422


async def test_ca_pem_is_held_in_the_record(client, auth_headers, policy):
    pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
    await _post(client, auth_headers, _item(upstream_tls={"verify": True, "ca_pem": pem}))
    got = (await client.get(f"{URL}/aa", headers=auth_headers)).json()
    assert got["upstream_tls"] == {"verify": True, "ca_pem": pem}


async def test_attach_needs_platform_admin(client, policy):
    r = await client.post(URL, json=[_item()], headers={"X-API-Key": "not-a-key"})
    assert r.status_code in (401, 403)
    r = await client.get(URL)
    assert r.status_code in (401, 403)


async def test_the_list_is_paginated(client, auth_headers, policy):
    await _post(client, auth_headers, *[_item(name=f"app{i}") for i in range(3)])
    page = (await client.get(URL, params={"page_size": 2}, headers=auth_headers)).json()
    assert [a["name"] for a in page["items"]] == ["app0", "app1"]
    assert (page["total"], page["pages"]) == (3, 2)
    last = (await client.get(URL, params={"page_size": 2, "page": 2}, headers=auth_headers)).json()
    assert [a["name"] for a in last["items"]] == ["app2"]


async def test_a_partial_update_cannot_widen_access(client, auth_headers, policy):
    await _post(client, auth_headers, _item(allowed_groups=["aa-users"]))
    r = await client.post(URL, json=[{"name": "aa", "upstream": "https://aa.apps.example"}], headers=auth_headers)
    assert r.status_code == 422
    doc = await AppAttachment.find_one(AppAttachment.name == "aa")
    assert doc.allowed_groups == ["aa-users"]


async def test_a_concurrent_create_is_a_per_item_conflict(client, auth_headers, policy, monkeypatch):
    # Another request inserted "aa" between this one's read and its insert.
    await AppAttachment(name="aa", upstream="https://aa.apps.example", auth_required=True, allowed_groups=[]).insert()
    real_find_one = AppAttachment.find_one

    def find_nothing(*args, **kwargs):
        class _Nothing:
            def __await__(self):
                async def none():
                    return None
                return none().__await__()
        return _Nothing()

    monkeypatch.setattr(AppAttachment, "find_one", find_nothing)
    body = await _post(client, auth_headers, _item(), _item(name="bb"))
    monkeypatch.setattr(AppAttachment, "find_one", real_find_one)
    assert [r["status"] for r in body["results"]] == ["error", "created"]
    assert body["results"][0]["error_code"] == "concurrency_conflict"


async def test_a_managed_attachment_is_refused(client, auth_headers, policy):
    await AppAttachment(
        name="aa", upstream="https://aa.apps.example", auth_required=True, allowed_groups=[],
        managed_by="wip-deploy",
    ).insert()
    res = (await _post(client, auth_headers, _item(display_name="changed")))["results"][0]
    assert res["error_code"] == "managed_by_deployer"
    r = await client.post(f"{URL}/deactivate", json=[{"name": "aa"}], headers=auth_headers)
    assert r.json()["results"][0]["error_code"] == "managed_by_deployer"
    assert (await AppAttachment.find_one(AppAttachment.name == "aa")).status == "active"


async def test_the_app_edge_may_read_and_nothing_else(client, auth_headers, policy):
    from registry.main import providers as app_providers
    from wip_auth import APIKeyProvider
    from wip_auth.models import APIKeyRecord
    from wip_auth.providers.api_key import hash_api_key

    await _post(client, auth_headers, _item())
    provider = next(p for p in app_providers if isinstance(p, APIKeyProvider))
    edge_key = "edge-test-key-1051"
    record = APIKeyRecord(
        name="wip-app-edge-test", key_hash=hash_api_key(edge_key), owner="system",
        groups=["wip-app-edge"], namespaces=["wip-canary-unassigned"],
    )
    provider.add_key(record)
    try:
        edge = {"X-API-Key": edge_key}
        listed = await client.get(URL, headers=edge)
        assert listed.status_code == 200 and listed.json()["items"][0]["name"] == "aa"
        assert (await client.get(f"{URL}/aa", headers=edge)).status_code == 200
        assert (await client.post(URL, json=[_item(name="bb")], headers=edge)).status_code == 403
        assert (await client.post(f"{URL}/deactivate", json=[{"name": "aa"}], headers=edge)).status_code == 403
        assert (await client.get("/api/registry/api-keys", headers=edge)).status_code == 403
    finally:
        provider.remove_key(record.key_hash)


async def test_the_edge_group_counts_only_for_an_api_key(client, monkeypatch, policy):
    """A login whose identity provider sends a group named like the edge's
    gains no read access; an admin login keeps it."""
    from registry.services import auth as registry_auth
    from wip_auth.models import UserIdentity

    def as_identity(identity):
        def require_groups(groups):
            async def dependency(_request):
                if not identity.has_any_group(groups):
                    from fastapi import HTTPException
                    raise HTTPException(status_code=403, detail="no")
                return identity
            return dependency
        return require_groups

    oidc_edge = UserIdentity(user_id="u", username="u", groups=["wip-app-edge"], auth_method="jwt")
    monkeypatch.setattr(registry_auth, "require_groups", as_identity(oidc_edge))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        await registry_auth.require_attachment_reader(None)
    assert e.value.status_code == 403

    oidc_admin = UserIdentity(user_id="a", username="a", groups=["wip-admins"], auth_method="jwt")
    monkeypatch.setattr(registry_auth, "require_groups", as_identity(oidc_admin))
    assert (await registry_auth.require_attachment_reader(None)) is oidc_admin


async def test_install_names_resolve_concurrently(monkeypatch):
    import asyncio
    import time

    async def slow(host):
        await asyncio.sleep(0.2)
        return set()

    monkeypatch.setattr(attach_service, "_resolve", slow)
    policy = attach_service.AttachPolicy(blocked_hosts=[f"h{i}" for i in range(10)])
    started = time.monotonic()
    await attach_service.resolve_install_addresses(policy)
    assert time.monotonic() - started < 1.0
