"""The system backup carries app attachments, and a restore puts them back.

Attachments are restored last, by name, with their archived status, under
the attach-time rules: a name the install's own apps route under, an invalid
upstream, or one resolving to a blocked target is skipped and listed; an
upstream that does not resolve yet is written and listed as unresolved.
"""

import ipaddress
import json

import pytest

from registry.models.api_key import StoredAPIKey
from registry.models.attachment import AppAttachment
from registry.models.grant import NamespaceGrant
from registry.services import attachments as attach_service
from registry.services import key_shadowing

ATTACH = "/api/registry/attachments"
BACKUP = "/api/registry/system-backup"
RESTORE = "/api/registry/system-restore"

DNS = {
    "aa.apps.example": {"203.0.113.10"},
    "bb.apps.example": {"203.0.113.11"},
    "wip-registry": {"10.89.0.5"},
    "moved.example": {"203.0.113.12"},
    "sneaky.example": {"169.254.169.254"},
}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    async def _resolve(host: str):
        try:
            return {ipaddress.ip_address(host)}
        except ValueError:
            return {ipaddress.ip_address(a) for a in DNS.get(host, set())}

    monkeypatch.setattr(attach_service, "_resolve", _resolve)


def _write_policy(path, reserved=("kb", "rc")):
    path.write_text(json.dumps({"blocked_hosts": ["wip-registry"], "reserved_names": list(reserved)}))


@pytest.fixture
def policy(tmp_path, monkeypatch):
    path = tmp_path / "attach-policy.json"
    _write_policy(path)
    monkeypatch.setenv(attach_service.ATTACH_POLICY_FILE_ENV, str(path))
    return path


@pytest.fixture
async def fresh(client):
    await NamespaceGrant.delete_all()
    key_shadowing.reset_for_tests()
    yield client
    key_shadowing.reset_for_tests()


def _item(name, upstream, **extra):
    return {"name": name, "upstream": upstream, "auth_required": True, "allowed_groups": [], **extra}


async def _attach(client, headers, *items):
    r = await client.post(ATTACH, json=list(items), headers=headers)
    assert r.status_code == 200, r.text
    assert all(x["status"] in ("created", "updated") for x in r.json()["results"]), r.text


async def _backup(client, headers) -> dict:
    r = await client.post(BACKUP, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _restore(client, headers, archive, **params) -> dict:
    query = {k: str(v).lower() for k, v in params.items()}
    r = await client.post(RESTORE, json=archive, params=query, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_round_trip_puts_attachments_back_with_their_status(fresh, auth_headers, policy):
    client = fresh
    await _attach(
        client, auth_headers,
        _item("aa", "https://aa.apps.example", allowed_groups=["wip-users"], display_name="AA",
              strip_prefix=True, preserve_prefix_subpaths=["/apps/aa/static"]),
        _item("bb", "http://bb.apps.example:8080"),
    )
    r = await client.post(f"{ATTACH}/deactivate", json=[{"name": "bb"}], headers=auth_headers)
    assert r.json()["results"][0]["status"] == "deactivated"

    archive = await _backup(client, auth_headers)
    assert [a["name"] for a in archive["attachments"]] == ["aa", "bb"]
    assert archive["attachments"][0]["display_name"] == "AA"

    await AppAttachment.delete_all()
    dry = await _restore(client, auth_headers, archive)
    assert dry["attachments"]["created"] == ["aa", "bb"]
    assert await AppAttachment.find_all().count() == 0

    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert report["attachments"]["created"] == ["aa", "bb"]
    assert report["attachments"]["unresolved"] == []
    aa = await AppAttachment.find_one(AppAttachment.name == "aa")
    bb = await AppAttachment.find_one(AppAttachment.name == "bb")
    assert (aa.status, bb.status) == ("active", "inactive")
    assert aa.allowed_groups == ["wip-users"] and aa.strip_prefix
    assert aa.preserve_prefix_subpaths == ["/apps/aa/static"]
    assert aa.managed_by is None

    again = await _restore(client, auth_headers, archive, dry_run=False)
    assert again["attachments"]["unchanged"] == ["aa", "bb"]

    # The served list (what the app edge reads) has them back.
    listed = (await client.get(ATTACH, headers=auth_headers)).json()["items"]
    assert {(a["name"], a["status"]) for a in listed} == {("aa", "active"), ("bb", "inactive")}


@pytest.mark.asyncio
async def test_a_difference_needs_overwrite(fresh, auth_headers, policy):
    client = fresh
    await _attach(client, auth_headers, _item("aa", "https://aa.apps.example"))
    archive = await _backup(client, auth_headers)
    await _attach(client, auth_headers, _item("aa", "https://bb.apps.example"))

    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert "differs" in report["attachments"]["skipped"][0]["reason"]
    assert (await AppAttachment.find_one(AppAttachment.name == "aa")).upstream == "https://bb.apps.example"

    report = await _restore(client, auth_headers, archive, dry_run=False, overwrite=True)
    assert report["attachments"]["replaced"] == ["aa"]
    assert (await AppAttachment.find_one(AppAttachment.name == "aa")).upstream == "https://aa.apps.example"


@pytest.mark.asyncio
async def test_attach_rules_hold_on_restore(fresh, auth_headers, policy, monkeypatch):
    client = fresh
    await _attach(
        client, auth_headers,
        _item("aa", "https://aa.apps.example"),
        _item("later", "https://later.apps.example"),  # does not resolve: attached with a warning
        _item("moved", "https://moved.example"),
    )
    archive = await _backup(client, auth_headers)
    # Since the backup, "moved.example" points into the install.
    monkeypatch.setitem(DNS, "moved.example", {"10.89.0.5"})
    archive["attachments"].append(dict(archive["attachments"][0], name="kb"))
    archive["attachments"].append(dict(archive["attachments"][0], name="meta", upstream="https://sneaky.example"))
    await AppAttachment.delete_all()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    out = report["attachments"]
    skipped = {s["id"]: s["reason"] for s in out["skipped"]}
    assert "route of an app this install deploys" in skipped["kb"]
    assert "upstream refused" in skipped["meta"]
    assert "upstream refused" in skipped["moved"]
    assert sorted(out["created"]) == ["aa", "later"]
    assert out["unresolved"] == ["later"]
    names = {a.name for a in await AppAttachment.find_all().to_list()}
    assert names == {"aa", "later"}


@pytest.mark.asyncio
async def test_a_managed_attachment_on_the_target_is_never_replaced(fresh, auth_headers, policy):
    client = fresh
    await _attach(client, auth_headers, _item("aa", "https://aa.apps.example"))
    archive = await _backup(client, auth_headers)
    doc = await AppAttachment.find_one(AppAttachment.name == "aa")
    doc.managed_by = "wip-deploy"
    doc.upstream = "https://bb.apps.example"
    await doc.save()

    report = await _restore(client, auth_headers, archive, dry_run=False, overwrite=True)
    assert report["attachments"]["skipped"][0]["reason"] == "managed by wip-deploy on this instance"
    assert (await AppAttachment.find_one(AppAttachment.name == "aa")).upstream == "https://bb.apps.example"


@pytest.mark.asyncio
async def test_an_unusable_policy_file_skips_every_attachment(fresh, auth_headers, policy):
    client = fresh
    await _attach(client, auth_headers, _item("aa", "https://aa.apps.example"))
    archive = await _backup(client, auth_headers)
    await AppAttachment.delete_all()
    policy.write_text("")

    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert "attach policy unavailable" in report["attachments"]["skipped"][0]["reason"]
    assert await AppAttachment.find_all().count() == 0


@pytest.mark.asyncio
async def test_an_archive_without_attachments_still_restores(fresh, auth_headers, policy):
    client = fresh
    archive = await _backup(client, auth_headers)
    archive.pop("attachments")
    report = await _restore(client, auth_headers, archive)
    assert report["attachments"] == {
        "created": [], "replaced": [], "unchanged": [], "skipped": [], "orphaned": [], "unresolved": [],
    }


@pytest.mark.asyncio
async def test_an_invalid_entry_is_skipped_and_the_rest_restores(fresh, auth_headers, policy):
    """An entry failing the attach rules is skipped on its own; the keys and
    the other attachments are restored all the same."""
    client = fresh
    await _attach(client, auth_headers, _item("aa", "https://aa.apps.example"))
    r = await client.post(
        "/api/registry/api-keys", headers=auth_headers,
        json={"name": "rc-key-1055", "namespaces": ["default"]},
    )
    assert r.status_code == 201, r.text
    archive = await _backup(client, auth_headers)
    archive["attachments"].append(
        dict(archive["attachments"][0], name="bad", preserve_prefix_subpaths=["/apps/other/x"])
    )
    archive["attachments"].append(dict(archive["attachments"][0], name="odd", status="paused"))
    await AppAttachment.delete_all()
    await StoredAPIKey.delete_all()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    skipped = {s["id"]: s["reason"] for s in report["attachments"]["skipped"]}
    assert "fails the attach rules" in skipped["bad"] and "preserve_prefix_subpaths" in skipped["bad"]
    assert "neither 'active' nor 'inactive'" in skipped["odd"]
    assert report["attachments"]["created"] == ["aa"]
    assert "rc-key-1055" in report["api_keys"]["created"]


@pytest.mark.asyncio
async def test_a_stored_record_that_predates_a_rule_does_not_stop_the_backup(
    fresh, auth_headers, policy,
):
    """The stored record has no subpath rule; the attach request does. A
    record written before the rule is archived as stored, and skipped on
    restore."""
    client = fresh
    await AppAttachment(
        name="old", upstream="https://aa.apps.example", auth_required=True, allowed_groups=[],
        strip_prefix=True, preserve_prefix_subpaths=["/apps/elsewhere/x"],
    ).insert()
    archive = await _backup(client, auth_headers)
    assert [a["name"] for a in archive["attachments"]] == ["old"]
    await AppAttachment.delete_all()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert report["attachments"]["skipped"][0]["id"] == "old"
    assert await AppAttachment.find_all().count() == 0


@pytest.mark.asyncio
async def test_an_entry_the_sources_deployer_manages_is_not_restored(fresh, auth_headers, policy):
    client = fresh
    await _attach(client, auth_headers, _item("aa", "https://aa.apps.example"))
    archive = await _backup(client, auth_headers)
    archive["attachments"][0]["managed_by"] = "wip-deploy"
    await AppAttachment.delete_all()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert "managed by wip-deploy on the source" in report["attachments"]["skipped"][0]["reason"]
    assert await AppAttachment.find_all().count() == 0
