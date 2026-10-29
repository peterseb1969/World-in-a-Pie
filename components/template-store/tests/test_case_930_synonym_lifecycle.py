"""CASE-930 — a template's retirement and revival reach the Registry.

A template keeps one Registry entry across its versions, so it is retired
only when no version is active. Then its synonyms are released — they stay
recorded as inactive and neither block another template from claiming the
key nor resolve. Its {ns, type, value} key is the entry's primary key, never
released, so a retired template stays addressable by value. As soon as a
version is active again
(reactivate, a re-post that creates a new version, a draft activated) they
are claimed again.

Deactivating goes through the service: the delete route's document-dependency
check needs document-store, which is not in this harness.
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient

from registry.main import app as registry_app
from registry.models.entry import RegistryEntry
from template_store.services.template_service import TemplateService

NS = "wip"
VENDOR = {"vendor_code": "V-930"}


def _declaration(value: str, label: str | None = None) -> dict:
    return {
        "namespace": NS, "value": value, "label": label or value,
        "identity_fields": ["code"],
        "fields": [{"name": "code", "label": "Code", "type": "string", "mandatory": True}],
    }


async def _post(client: AsyncClient, auth_headers: dict, declaration: dict) -> dict:
    resp = await client.post("/api/template-store/templates", headers=auth_headers, json=[declaration])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


async def _registry(path: str, body: list[dict]) -> dict:
    async with AsyncClient(transport=ASGITransport(app=registry_app), base_url="http://registry") as r:
        resp = await r.post(
            f"/api/registry{path}", json=body,
            headers={"X-API-Key": os.environ["MASTER_API_KEY"]},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


async def _template_with_vendor_synonym(client, auth_headers, value: str) -> str:
    created = await _post(client, auth_headers, _declaration(value))
    assert created["status"] == "created", created
    added = await _registry("/synonyms/add", [{
        "target_id": created["id"], "synonym_namespace": NS,
        "synonym_entity_type": "templates", "synonym_composite_key": VENDOR,
    }])
    assert added["status"] == "added", added
    return created["id"]


async def _states(template_id: str) -> dict[str, str]:
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == template_id)
    assert entry is not None
    [vendor] = [s.status for s in entry.synonyms if "vendor_code" in s.composite_key]
    return {"vendor": vendor}


@pytest.mark.asyncio
async def test_retiring_the_last_version_releases_and_reactivating_reclaims(
    client: AsyncClient, auth_headers: dict,
):
    tid = await _template_with_vendor_synonym(client, auth_headers, "TPL930_A")
    assert await _states(tid) == {"vendor": "active"}

    assert await TemplateService.delete_template(tid, version=1) is True
    assert await _states(tid) == {"vendor": "inactive"}

    resp = await client.post(
        f"/api/template-store/templates/{tid}/reactivate?namespace={NS}&version=1",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert await _states(tid) == {"vendor": "active"}


@pytest.mark.asyncio
async def test_an_active_version_keeps_the_synonyms(client: AsyncClient, auth_headers: dict):
    tid = await _template_with_vendor_synonym(client, auth_headers, "TPL930_B")
    updated = await _post(client, auth_headers, _declaration("TPL930_B", label="second"))
    assert updated["status"] == "updated" and updated["version"] == 2

    assert await TemplateService.delete_template(tid, version=2) is True
    assert await _states(tid) == {"vendor": "active"}

    assert await TemplateService.delete_template(tid, version=1) is True
    assert await _states(tid) == {"vendor": "inactive"}


@pytest.mark.asyncio
async def test_a_new_version_of_a_retired_template_reclaims(client: AsyncClient, auth_headers: dict):
    tid = await _template_with_vendor_synonym(client, auth_headers, "TPL930_C")
    assert await TemplateService.delete_template(tid, version=1) is True
    assert await _states(tid) == {"vendor": "inactive"}

    reposted = await _post(client, auth_headers, _declaration("TPL930_C", label="back"))
    assert reposted["status"] == "updated", reposted
    assert reposted["id"] == tid
    assert await _states(tid) == {"vendor": "active"}


@pytest.mark.asyncio
async def test_reconcile_releases_templates_retired_before(client: AsyncClient, auth_headers: dict):
    from template_store.models.template import Template

    tid = await _template_with_vendor_synonym(client, auth_headers, "TPL930_D")
    # retired without the Registry hearing of it (as before this change)
    t = await Template.find_one({"template_id": tid, "version": 1})
    t.status = "inactive"
    await t.save()
    assert await _states(tid) == {"vendor": "active"}

    async def _reconcile(dry_run: bool) -> dict:
        resp = await client.post(
            "/api/template-store/maintenance/reconcile-synonym-lifecycle",
            headers=auth_headers, json={"namespace": NS, "dry_run": dry_run},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    dry = await _reconcile(True)
    assert dry["released"] == 1
    assert dry["items"] == [{"entry_id": tid, "status": "released", "keys": [VENDOR], "detail": None}]
    assert await _states(tid) == {"vendor": "active"}

    assert (await _reconcile(False))["released"] == 1
    assert await _states(tid) == {"vendor": "inactive"}
    assert (await _reconcile(False))["unchanged"] == 1
