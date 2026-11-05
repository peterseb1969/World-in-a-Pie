"""Attachment reads say whether the install serves the record.

`served` is true when the record is active and the attach policy says an app
edge runs; otherwise `not_served_reason` names why: `inactive`, `no_app_edge`,
or `policy_unavailable` (the edge then serves nothing either). A read never
fails on the policy file.
"""

import ipaddress
import json

import pytest

from registry.services import attachments as attach_service

ATTACH = "/api/registry/attachments"


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    async def _resolve(host: str):
        return {ipaddress.ip_address("203.0.113.10")}

    monkeypatch.setattr(attach_service, "_resolve", _resolve)


def _write(path, **fields):
    path.write_text(json.dumps({"blocked_hosts": [], "reserved_names": [], **fields}))


@pytest.fixture
def policy(tmp_path, monkeypatch):
    path = tmp_path / "attach-policy.json"
    _write(path, app_edge=True)
    monkeypatch.setenv(attach_service.ATTACH_POLICY_FILE_ENV, str(path))
    return path


async def _attach(client, headers, *names):
    r = await client.post(ATTACH, headers=headers, json=[
        {"name": n, "upstream": f"https://{n}.apps.example", "auth_required": True, "allowed_groups": []}
        for n in names
    ])
    assert all(x["status"] == "created" for x in r.json()["results"]), r.text


async def _served(client, headers) -> dict[str, tuple[bool, str | None]]:
    r = await client.get(ATTACH, headers=headers)
    assert r.status_code == 200, r.text
    listed = {a["name"]: (a["served"], a["not_served_reason"]) for a in r.json()["items"]}
    for name, state in listed.items():
        one = (await client.get(f"{ATTACH}/{name}", headers=headers)).json()
        assert (one["served"], one["not_served_reason"]) == state
    return listed


@pytest.mark.asyncio
async def test_served_follows_status_and_the_edge(client, auth_headers, policy):
    await _attach(client, auth_headers, "aa", "bb")
    r = await client.post(f"{ATTACH}/deactivate", headers=auth_headers, json=[{"name": "bb"}])
    assert r.json()["results"][0]["status"] == "deactivated"

    assert await _served(client, auth_headers) == {
        "aa": (True, None), "bb": (False, "inactive"),
    }

    _write(policy, app_edge=False)
    assert await _served(client, auth_headers) == {
        "aa": (False, "no_app_edge"), "bb": (False, "inactive"),
    }


@pytest.mark.asyncio
async def test_an_unusable_policy_reads_as_not_served_and_never_fails_a_read(
    client, auth_headers, policy,
):
    await _attach(client, auth_headers, "aa")
    policy.write_text("")
    assert await _served(client, auth_headers) == {"aa": (False, "policy_unavailable")}


@pytest.mark.asyncio
async def test_a_policy_without_the_app_edge_field_means_served(client, auth_headers, policy):
    """Rendered before the edge was optional, when every install ran it."""
    await _attach(client, auth_headers, "aa")
    _write(policy)
    assert await _served(client, auth_headers) == {"aa": (True, None)}
