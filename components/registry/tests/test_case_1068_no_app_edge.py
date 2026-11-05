"""Attachments on an install without the app edge (CASE-1068).

The app edge is an optional module; the deployer renders whether it runs into
the attach policy file (`app_edge`). Without it nothing serves an attachment,
so every attachment write is refused (invalid_state, rule no_app_edge) while
reads and deactivation keep working, and a restore skips the archive's active
attachments. A policy file without the field (rendered before the module
existed, when every install ran the edge) means the edge runs.
"""

import ipaddress
import json

import pytest

from registry.models.attachment import AppAttachment
from registry.models.grant import NamespaceGrant
from registry.services import attachments as attach_service
from registry.services import key_shadowing

ATTACH = "/api/registry/attachments"
BACKUP = "/api/registry/system-backup"
RESTORE = "/api/registry/system-restore"

DNS = {"aa.apps.example": {"203.0.113.10"}, "bb.apps.example": {"203.0.113.11"}}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    async def _resolve(host: str):
        try:
            return {ipaddress.ip_address(host)}
        except ValueError:
            return {ipaddress.ip_address(a) for a in DNS.get(host, set())}

    monkeypatch.setattr(attach_service, "_resolve", _resolve)


def _write_policy(path, **extra):
    path.write_text(json.dumps({"blocked_hosts": ["wip-registry"], "reserved_names": ["kb"], **extra}))


@pytest.fixture
def policy(tmp_path, monkeypatch):
    path = tmp_path / "attach-policy.json"
    _write_policy(path, app_edge=True)
    monkeypatch.setenv(attach_service.ATTACH_POLICY_FILE_ENV, str(path))
    return path


@pytest.fixture
async def fresh(client):
    await NamespaceGrant.delete_all()
    key_shadowing.reset_for_tests()
    yield client
    key_shadowing.reset_for_tests()


def _item(name="aa", upstream="https://aa.apps.example", **extra):
    return {"name": name, "upstream": upstream, "auth_required": True, "allowed_groups": [], **extra}


async def _post(client, headers, *items, path=ATTACH):
    r = await client.post(path, json=list(items), headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_without_the_edge_every_write_is_refused(client, auth_headers, policy):
    await _post(client, auth_headers, _item())  # attached while the edge ran
    _write_policy(policy, app_edge=False)

    body = await _post(client, auth_headers, _item(), _item("bb", "https://bb.apps.example"))
    assert body["failed"] == 2
    for res in body["results"]:
        assert (res["status"], res["error_code"], res["details"]["rule"]) == (
            "error", "invalid_state", "no_app_edge",
        )
        assert "add-module app-edge" in res["error"]
    assert await AppAttachment.find_one(AppAttachment.name == "bb") is None


async def test_without_the_edge_reads_and_deactivate_still_work(client, auth_headers, policy):
    await _post(client, auth_headers, _item())
    _write_policy(policy, app_edge=False)

    got = await client.get(f"{ATTACH}/aa", headers=auth_headers)
    assert got.status_code == 200 and got.json()["status"] == "active"
    listed = (await client.get(ATTACH, headers=auth_headers)).json()
    assert [a["name"] for a in listed["items"]] == ["aa"]

    body = await _post(client, auth_headers, {"name": "aa"}, path=f"{ATTACH}/deactivate")
    assert body["results"][0]["status"] == "deactivated"

    # Reviving is a write: refused until the edge is back.
    revived = await _post(client, auth_headers, _item())
    assert revived["results"][0]["details"]["rule"] == "no_app_edge"
    _write_policy(policy, app_edge=True)
    revived = await _post(client, auth_headers, _item())
    assert revived["results"][0]["status"] == "updated"


async def test_a_policy_without_the_field_means_the_edge_runs(client, auth_headers, policy):
    _write_policy(policy)
    assert attach_service.load_policy().app_edge is True
    body = await _post(client, auth_headers, _item())
    assert body["results"][0]["status"] == "created"


@pytest.mark.asyncio
async def test_a_restore_without_the_edge_skips_active_attachments(fresh, auth_headers, policy):
    client = fresh
    await _post(client, auth_headers, _item(), _item("bb", "https://bb.apps.example"))
    await _post(client, auth_headers, {"name": "bb"}, path=f"{ATTACH}/deactivate")
    r = await client.post(BACKUP, headers=auth_headers)
    assert r.status_code == 200, r.text
    archive = r.json()

    await AppAttachment.delete_all()
    _write_policy(policy, app_edge=False)
    r = await client.post(RESTORE, json=archive, params={"dry_run": "false"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    out = r.json()["attachments"]

    assert out["created"] == ["bb"]  # an inactive record is restored as a record
    assert [(s["name"], s["reason_code"]) for s in out["skipped"]] == [("aa", "no_app_edge")]
    assert await AppAttachment.find_one(AppAttachment.name == "aa") is None
    bb = await AppAttachment.find_one(AppAttachment.name == "bb")
    assert bb.status == "inactive"
