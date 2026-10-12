"""CASE-858 — the heal pass for legacy null-resolved edge-type endpoints.

Edge types declared before endpoints carried a resolved half serve
`resolved: null` on every entry, and the natural heal triggers (widen,
activation, restore repair) never fire for established edge types.
POST /maintenance/heal-endpoints is the missing trigger: idempotent,
dry-run first, resolves each null entry in the template's own namespace,
reports what it cannot resolve and leaves those null (fail-closed).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from template_store.models.template import Template

from .test_relationship_templates import (
    API,
    _ensure_endpoint_templates,
    _post_template,
    _relationship_template,
    _template_id,
)


async def _create_edge_type(client, auth_headers, value: str) -> dict:
    await _ensure_endpoint_templates(client, auth_headers, "EXPERIMENT", "MOLECULE")
    item = await _post_template(
        client, auth_headers,
        _relationship_template(value=value, source_templates=["EXPERIMENT"],
                               target_templates=["MOLECULE"]),
    )
    assert item["status"] in ("created", "updated"), item
    return item


async def _forge_legacy_nulls(value: str) -> None:
    """Null the resolved halves in storage — the pre-two-half row shape."""
    async for row in Template.find({"value": value, "namespace": "wip"}):
        for entry in (row.source_templates or []) + (row.target_templates or []):
            entry.resolved = None
        await row.save()


async def _heal(client, auth_headers, dry_run: bool):
    resp = await client.post(
        f"{API}/maintenance/heal-endpoints",
        headers=auth_headers,
        json={"namespace": "wip", "dry_run": dry_run},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_heal_fills_legacy_null_resolved_entries(client: AsyncClient, auth_headers: dict):
    await _create_edge_type(client, auth_headers, "EDGE_858_HEAL")
    await _forge_legacy_nulls("EDGE_858_HEAL")

    # Dry run reports, changes nothing.
    report = await _heal(client, auth_headers, dry_run=True)
    mine = [i for i in report["items"] if i["value"] == "EDGE_858_HEAL"]
    assert mine and len(mine[0]["healed"]) == 2, report
    stored = await Template.find_one({"value": "EDGE_858_HEAL", "namespace": "wip"})
    assert all(
        e.resolved is None
        for e in stored.source_templates + stored.target_templates
    ), "dry run must not write"

    # Apply fills the resolved halves with the canonical ids.
    report = await _heal(client, auth_headers, dry_run=False)
    assert report["entries_healed"] >= 2
    stored = await Template.find_one({"value": "EDGE_858_HEAL", "namespace": "wip"})
    exp_id = await _template_id(client, auth_headers, "EXPERIMENT")
    mol_id = await _template_id(client, auth_headers, "MOLECULE")
    assert [e.resolved for e in stored.source_templates] == [exp_id]
    assert [e.resolved for e in stored.target_templates] == [mol_id]

    # Idempotent: a second pass has nothing to do.
    report = await _heal(client, auth_headers, dry_run=False)
    assert not [i for i in report["items"] if i["value"] == "EDGE_858_HEAL"]


@pytest.mark.asyncio
async def test_heal_reports_unresolvable_and_leaves_them_null(client: AsyncClient, auth_headers: dict):
    await _create_edge_type(client, auth_headers, "EDGE_858_GONE")
    # Forge one legacy entry whose anchor no longer resolves anywhere.
    async for row in Template.find({"value": "EDGE_858_GONE", "namespace": "wip"}):
        row.source_templates[0].resolved = None
        row.source_templates[0].lookup_value = "NEVER_EXISTED_858"
        await row.save()

    report = await _heal(client, auth_headers, dry_run=False)
    mine = [i for i in report["items"] if i["value"] == "EDGE_858_GONE"]
    assert mine, report
    assert mine[0]["unresolvable"] == [
        {"list": "source_templates", "lookup_value": "NEVER_EXISTED_858"}
    ]
    stored = await Template.find_one({"value": "EDGE_858_GONE", "namespace": "wip"})
    assert stored.source_templates[0].resolved is None  # left null, never dropped
