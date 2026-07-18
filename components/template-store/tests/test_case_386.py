"""Tests for CASE-386 — template-store endpoint permission-enforcement.

CASE-386 added `check_namespace_permission` to the 11 template-store
endpoints that previously skipped it (sibling of CASE-384's def-store /
document-store work). These tests verify two things per endpoint:

1. Admin-passthrough — admin keys bypass via the superadmin short-circuit,
   so the new gates must be transparent to admin callers (regression
   protection: we didn't break the happy path).
2. Gate-wiring — the endpoint actually calls into the auth layer. We patch
   `check_namespace_permission` to raise 404 unconditionally; if the gate is
   wired the response is 404, if it's missing the endpoint still returns 200.

The denial *logic* is covered by CASE-351's Registry-side tests; here we only
assert the API endpoints call into the auth layer at the right point.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

BASE = "/api/template-store/templates"


async def _seed(client: AsyncClient, auth_headers: dict, value: str) -> str:
    """Create a minimal template in the wip namespace; return its template_id."""
    resp = await client.post(
        BASE,
        json=[{"namespace": "wip", "value": value, "label": f"{value} label"}],
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]["id"]


# Each spec: (label, callable(client, headers, tid, value) -> response).
# One spec per newly-gated endpoint.
def _specs():
    async def get_template(c, h, tid, v):
        return await c.get(f"{BASE}/{tid}", headers=h)

    async def get_template_raw(c, h, tid, v):
        return await c.get(f"{BASE}/{tid}/raw", headers=h)

    async def get_template_by_value_raw(c, h, tid, v):
        return await c.get(f"{BASE}/by-value/{v}/raw", params={"namespace": "wip"}, headers=h)

    async def get_template_by_value_and_version(c, h, tid, v):
        return await c.get(f"{BASE}/by-value/{v}/versions/1", headers=h)

    async def update_templates(c, h, tid, v):
        return await c.put(BASE, json=[{"template_id": tid, "label": "updated"}], headers=h)

    async def get_template_dependencies(c, h, tid, v):
        return await c.get(f"{BASE}/{tid}/dependencies", headers=h)

    async def delete_templates(c, h, tid, v):
        return await c.request("DELETE", BASE, json=[{"id": tid}], headers=h)

    async def validate_template(c, h, tid, v):
        return await c.post(f"{BASE}/{tid}/validate", headers=h)

    async def cascade_template(c, h, tid, v):
        return await c.post(f"{BASE}/{tid}/cascade", headers=h)

    async def get_template_children(c, h, tid, v):
        return await c.get(f"{BASE}/{tid}/children", headers=h)

    async def get_template_descendants(c, h, tid, v):
        return await c.get(f"{BASE}/{tid}/descendants", headers=h)

    return [
        ("get_template", get_template),
        ("get_template_raw", get_template_raw),
        ("get_template_by_value_raw", get_template_by_value_raw),
        ("get_template_by_value_and_version", get_template_by_value_and_version),
        ("update_templates", update_templates),
        ("get_template_dependencies", get_template_dependencies),
        ("delete_templates", delete_templates),
        ("validate_template", validate_template),
        ("cascade_template", cascade_template),
        ("get_template_children", get_template_children),
        ("get_template_descendants", get_template_descendants),
    ]


SPECS = _specs()
_IDS = [label for label, _ in SPECS]


@pytest.mark.parametrize("label,call", SPECS, ids=_IDS)
@pytest.mark.asyncio
async def test_admin_passthrough(label, call, client: AsyncClient, auth_headers: dict):
    """Admin keys bypass the new gate (superadmin short-circuit) — every
    patched endpoint still returns 200 for an admin caller."""
    value = f"CASE386_ADMIN_{label.upper()}"
    tid = await _seed(client, auth_headers, value)

    resp = await call(client, auth_headers, tid, value)
    assert resp.status_code == 200, f"{label}: {resp.status_code} {resp.text}"


@pytest.mark.parametrize("label,call", SPECS, ids=_IDS)
@pytest.mark.asyncio
async def test_gate_is_wired(label, call, client: AsyncClient, auth_headers: dict):
    """The endpoint calls into check_namespace_permission. Patched to deny
    (404) unconditionally; a wired gate surfaces that 404, a missing gate
    would return 200."""
    value = f"CASE386_WIRED_{label.upper()}"
    tid = await _seed(client, auth_headers, value)

    async def deny(identity, namespace, level):
        raise HTTPException(status_code=404, detail="Namespace not found")

    with patch(
        "template_store.api.templates.check_namespace_permission",
        side_effect=deny,
    ):
        resp = await call(client, auth_headers, tid, value)
    assert resp.status_code == 404, f"{label} not gated: {resp.status_code} {resp.text}"
