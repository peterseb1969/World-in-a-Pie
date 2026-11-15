"""Grant fixes from a commit review.

An orphan made effective carries no namespace_id, whichever path makes it
effective; and a bulk revoke or activate counts every item that did not
succeed as failed, so succeeded + failed is the total.
"""

import uuid

import pytest

from registry.models.grant import NamespaceGrant

NS = "/api/registry/namespaces"


def _p(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


async def _orphan(prefix: str, subject: str) -> NamespaceGrant:
    return await NamespaceGrant(
        namespace=prefix, subject=subject, subject_type="api_key", permission="read",
        granted_by="t", orphaned=True, namespace_id="ns-elsewhere",
    ).insert()


@pytest.mark.asyncio
async def test_a_platform_admin_setting_a_keys_grant_clears_the_orphans_namespace_id(client):
    from registry.api.api_keys import _set_grant

    prefix, name = _p("ns"), _p("key")
    await _orphan(prefix, name)
    await _set_grant(name, prefix, "write")
    grant = await NamespaceGrant.find_one({"namespace": prefix, "subject": name})
    assert (grant.permission, grant.orphaned, grant.namespace_id) == ("write", False, None)


@pytest.mark.asyncio
async def test_revoking_an_unknown_grant_counts_as_failed(client, auth_headers):
    prefix = _p("ns")
    assert (await client.put(f"{NS}/{prefix}", json={}, headers=auth_headers)).status_code == 200
    resp = await client.request(
        "DELETE", f"{NS}/{prefix}/grants", headers=auth_headers,
        json=[{"subject": "nobody@example.com", "subject_type": "user"}],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["results"][0]["status"] == "not_found"
    assert (body["total"], body["succeeded"], body["failed"]) == (1, 0, 1)


@pytest.mark.asyncio
async def test_activating_an_unknown_grant_counts_as_failed(client, auth_headers):
    prefix, name = _p("ns"), _p("key")
    assert (await client.put(f"{NS}/{prefix}", json={}, headers=auth_headers)).status_code == 200
    await _orphan(prefix, name)
    resp = await client.post(
        f"{NS}/{prefix}/grants/activate", headers=auth_headers,
        json=[{"subject": name, "subject_type": "api_key"},
              {"subject": "nobody@example.com", "subject_type": "user"}],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r["status"] for r in body["results"]] == ["activated", "not_found"]
    assert (body["total"], body["succeeded"], body["failed"]) == (2, 1, 1)


@pytest.mark.asyncio
async def test_the_key_import_taking_over_an_orphan_clears_its_namespace_id(
    client, auth_headers, monkeypatch, tmp_path,
):
    import json

    from registry.services import key_import
    from registry.services.key_import import KEY_FILE_ENV

    from .test_case_1033_key_import import CANARY, _app_entry

    prefix, name = _p("ns"), _p("app")
    assert (await client.put(f"{NS}/{prefix}", json={}, headers=auth_headers)).status_code == 200
    await _orphan(prefix, name)
    path = tmp_path / "api-keys.json"
    path.write_text(json.dumps({"keys": [
        CANARY, _app_entry(name, f"plain-{uuid.uuid4().hex}", [prefix], {prefix: "write"}),
    ]}))
    monkeypatch.setenv(KEY_FILE_ENV, str(path))
    await key_import.run_pass()
    grant = await NamespaceGrant.find_one({"namespace": prefix, "subject": name})
    assert (grant.permission, grant.orphaned, grant.namespace_id) == ("write", False, None)
