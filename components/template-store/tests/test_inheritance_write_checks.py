"""Writes refuse an inheritance chain the read path could not resolve.

Resolution (the read path) refuses a chain deeper than the limit, a cycle or
a missing ancestor with 409 inheritance_unresolved. The write paths walk the
same pinned chain with the same walker, so a template is never accepted and
then served as unresolvable:
- create: refused before the Registry registers anything (a retry with a
  valid parent is not blocked by an orphan claim);
- a new version: the whole pinned chain, not each parent's latest version;
- draft activation: checked when the draft becomes active, with the
  activation set's parents read in their normalized (in-memory) form.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from template_store.services.inheritance_service import MAX_INHERITANCE_DEPTH

from .test_relationship_templates import API, _post_template


def _tpl(value: str, extends: str | None = None, **extra) -> dict:
    payload = {
        "namespace": "wip",
        "value": value,
        "label": value.title(),
        "fields": [{"name": f"f_{value.lower()}", "type": "string", "label": value}],
        **extra,
    }
    if extends:
        payload["extends"] = extends
        payload["extends_version"] = 1
    return payload


async def _chain(client: AsyncClient, auth_headers: dict, prefix: str, parents: int) -> list[str]:
    """Create a root plus `parents` descendants; the last has `parents` ancestors."""
    ids: list[str] = []
    for i in range(parents + 1):
        item = await _post_template(
            client, auth_headers, _tpl(f"{prefix}_{i}", ids[-1] if ids else None)
        )
        assert item["status"] == "created", item
        ids.append(item["id"])
    return ids


@pytest.mark.asyncio
async def test_create_at_the_depth_limit_is_accepted_and_resolves(client: AsyncClient, auth_headers: dict):
    ids = await _chain(client, auth_headers, "DEPTH_OK", MAX_INHERITANCE_DEPTH)
    resp = await client.get(f"{API}/templates/{ids[-1]}", headers=auth_headers)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_create_past_the_depth_limit_is_refused_without_a_registry_claim(
    client: AsyncClient, auth_headers: dict
):
    ids = await _chain(client, auth_headers, "DEPTH_X", MAX_INHERITANCE_DEPTH)

    item = await _post_template(client, auth_headers, _tpl("DEPTH_X_TOO_DEEP", ids[-1]))
    assert item["status"] == "error", item
    assert item["error_code"] == "invalid_state"
    assert item["details"]["rule"] == "max_depth"

    resp = await client.get(f"{API}/templates/by-value/DEPTH_X_TOO_DEEP", headers=auth_headers)
    assert resp.status_code == 404, resp.text
    # Nothing was registered: the same value with a shallower parent creates.
    item = await _post_template(client, auth_headers, _tpl("DEPTH_X_TOO_DEEP", ids[-2]))
    assert item["status"] == "created", item


@pytest.mark.asyncio
async def test_new_version_creating_a_cycle_is_refused(client: AsyncClient, auth_headers: dict):
    a = await _post_template(client, auth_headers, _tpl("CYC_A"))
    b = await _post_template(client, auth_headers, _tpl("CYC_B", a["id"]))
    assert b["status"] == "created", b

    # A v2 extends B v1, which extends A: a cycle by template_id.
    item = await _post_template(client, auth_headers, _tpl("CYC_A", b["id"]))
    assert item["status"] == "error", item
    assert item["error_code"] == "invalid_state"
    assert item["details"]["rule"] == "circular_inheritance"


@pytest.mark.asyncio
async def test_new_version_past_the_depth_limit_is_refused(client: AsyncClient, auth_headers: dict):
    ids = await _chain(client, auth_headers, "DEPTH_V", MAX_INHERITANCE_DEPTH)
    free = await _post_template(client, auth_headers, _tpl("DEPTH_V_FREE"))
    assert free["status"] == "created", free

    # A second version that moves under the deepest template.
    item = await _post_template(client, auth_headers, _tpl("DEPTH_V_FREE", ids[-1]))
    assert item["status"] == "error", item
    assert item["error_code"] == "invalid_state"
    assert item["details"]["rule"] == "max_depth"


@pytest.mark.asyncio
async def test_activation_past_the_depth_limit_is_refused_and_stays_draft(
    client: AsyncClient, auth_headers: dict
):
    ids = await _chain(client, auth_headers, "DEPTH_D", MAX_INHERITANCE_DEPTH)
    draft = await _post_template(
        client, auth_headers, _tpl("DEPTH_D_DRAFT", ids[-1], status="draft")
    )
    assert draft["status"] == "created", draft

    resp = await client.post(
        f"{API}/templates/{draft['id']}/activate", headers=auth_headers, params={"namespace": "wip"}
    )
    assert resp.status_code == 400, resp.text
    assert "depth" in resp.text

    raw = (await client.get(f"{API}/templates/{draft['id']}/raw", headers=auth_headers)).json()
    assert raw["status"] == "draft"


@pytest.mark.asyncio
async def test_activation_walks_set_parents_in_their_normalized_form(
    client: AsyncClient, auth_headers: dict
):
    """Three drafts chained by VALUE: the stored middle draft still names its
    parent by value, so only the normalized in-memory form can be walked."""
    g = await _post_template(client, auth_headers, _tpl("SETWALK_G", status="draft"))
    p = await _post_template(client, auth_headers, _tpl("SETWALK_P", "SETWALK_G", status="draft"))
    c = await _post_template(client, auth_headers, _tpl("SETWALK_C", "SETWALK_P", status="draft"))
    assert all(x["status"] == "created" for x in (g, p, c)), (g, p, c)

    resp = await client.post(
        f"{API}/templates/{c['id']}/activate", headers=auth_headers, params={"namespace": "wip"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_activated"] == 3

    resolved = (await client.get(f"{API}/templates/{c['id']}", headers=auth_headers)).json()
    assert {f["name"] for f in resolved["fields"]} == {"f_setwalk_g", "f_setwalk_p", "f_setwalk_c"}
