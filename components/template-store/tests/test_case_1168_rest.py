"""Health, migration verdicts and activation say what is true.

- /health answered 200 with "status": "unhealthy", so the image's
  HEALTHCHECK (curl -f) kept a container with no MongoDB healthy. It answers
  503 with the same body.
- A version event's migration verdict read a removed field document-store
  did not count (it counts up to 50) as empty, and offered the migration.
  An uncounted field is unknown: eligible null.
- Activation saved its set one member at a time; a failing save left a
  half-activated set behind an error that did not say so. The answer lists
  what was activated and what is still a draft.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from template_store.models.template import Template
from template_store.services.template_service import TemplateService

API = "/api/template-store/templates"


@pytest.mark.asyncio
async def test_an_unhealthy_service_is_not_a_200(client: AsyncClient, auth_headers):
    down = {"database": "disconnected", "registry": "connected", "def_store": "connected"}
    with patch("template_store.main._health_cache.get", new=AsyncMock(return_value=down)):
        resp = await client.get("/health")
    assert resp.status_code == 503, resp.text
    assert resp.json()["status"] == "unhealthy"


def test_a_removed_field_nobody_counted_is_unknown():
    removed = [f"f{i}" for i in range(51)]
    counts = {f: 0 for f in removed[:50]}  # document-store counts up to 50
    verdict = TemplateService._removed_fields_verdict(removed, counts, "via")
    assert verdict["eligible"] is None, verdict
    assert "f50" in verdict["reason"]


def test_counted_empty_removed_fields_are_eligible():
    verdict = TemplateService._removed_fields_verdict(["a", "b"], {"a": 0, "b": 0}, "via")
    assert verdict["eligible"] is True, verdict


@pytest.mark.asyncio
async def test_a_part_way_activation_says_what_it_did(client: AsyncClient, auth_headers):
    b = (await client.post(API, headers=auth_headers, json=[{
        "namespace": "wip", "value": "C1168R_B", "label": "B", "status": "draft",
        "fields": [{"name": "id", "type": "string", "label": "Id"}]}])).json()["results"][0]
    a = (await client.post(API, headers=auth_headers, json=[{
        "namespace": "wip", "value": "C1168R_A", "label": "A", "status": "draft",
        "fields": [{"name": "id", "type": "string", "label": "Id"},
                   {"name": "link", "type": "reference", "label": "L",
                    "reference_type": "document", "target_templates": ["C1168R_B"]}]}],
    )).json()["results"][0]

    real_save = Template.save
    calls = {"n": 0}

    async def save_then_fail(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("mongo went away")
        return await real_save(self, *args, **kwargs)

    with patch.object(Template, "save", save_then_fail):
        resp = await client.post(f"{API}/{a['id']}/activate", headers=auth_headers,
                                 params={"namespace": "wip"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_activated"] == 1, body
    assert body["errors"][0]["code"] == "activation_incomplete", body
    statuses = {d["value"]: d["status"] for d in body["activation_details"]}
    assert sorted(statuses.values()) == ["activated", "draft"], statuses
    stored = {t.value: t.status for t in await Template.find(
        {"template_id": {"$in": [a["id"], b["id"]]}}).to_list()}
    assert sorted(stored.values()) == ["active", "draft"], stored
