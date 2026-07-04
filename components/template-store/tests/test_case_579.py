"""Tests for CASE-579 — by-value read routes gate the omit-namespace path.

CASE-386 gated eight read handlers on the resolved entity's namespace, but
`get_template_by_value` and `get_template_versions` only checked permission
when the caller happened to pass `namespace` — omitting it searched every
namespace with no check at all. CASE-579 routes both through
`resolve_namespace_filter` (the `list_templates` contract): explicit
namespace → permission-checked single-namespace scope; omitted → search
restricted to the caller's accessible namespaces (superadmin unrestricted).

Three assertions per route, mirroring the CASE-386 test shape:

1. Admin-passthrough — with and without `namespace`, admin callers get 200.
2. Gate-wiring — `resolve_namespace_filter` patched to deny surfaces its 404
   on BOTH the explicit and the omitted-namespace call (the omitted path is
   the one CASE-579 fixed; pre-fix it returned 200).
3. Filter application — the NamespaceFilter query is actually merged into
   the Mongo query: scoping the filter to a foreign namespace hides a
   template that exists in `wip`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from wip_auth.permissions import NamespaceFilter

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


def _specs():
    async def by_value(c, h, v, params):
        return await c.get(f"{BASE}/by-value/{v}", params=params, headers=h)

    async def by_value_versions(c, h, v, params):
        return await c.get(f"{BASE}/by-value/{v}/versions", params=params, headers=h)

    return [
        ("get_template_by_value", by_value),
        ("get_template_versions", by_value_versions),
    ]


SPECS = _specs()
_IDS = [label for label, _ in SPECS]
_NS_PARAMS = [{}, {"namespace": "wip"}]
_NS_IDS = ["namespace_omitted", "namespace_explicit"]


@pytest.mark.parametrize("params", _NS_PARAMS, ids=_NS_IDS)
@pytest.mark.parametrize("label,call", SPECS, ids=_IDS)
@pytest.mark.asyncio
async def test_admin_passthrough(label, call, params, client: AsyncClient, auth_headers: dict):
    """Admin keys pass the gate (superadmin short-circuit) with or without
    an explicit namespace — the new gating is transparent to admin callers."""
    value = f"CASE579_ADMIN_{label.upper()}"
    await _seed(client, auth_headers, value)

    resp = await call(client, auth_headers, value, params)
    assert resp.status_code == 200, f"{label} {params}: {resp.status_code} {resp.text}"


@pytest.mark.parametrize("params", _NS_PARAMS, ids=_NS_IDS)
@pytest.mark.parametrize("label,call", SPECS, ids=_IDS)
@pytest.mark.asyncio
async def test_gate_is_wired(label, call, params, client: AsyncClient, auth_headers: dict):
    """Both routes call resolve_namespace_filter unconditionally. Patched to
    deny; a wired gate surfaces the 404 on the omitted-namespace call too —
    pre-CASE-579 that call skipped the auth layer entirely and returned 200."""
    value = f"CASE579_WIRED_{label.upper()}"
    await _seed(client, auth_headers, value)

    async def deny(identity, namespace, required="read"):
        raise HTTPException(status_code=404, detail="Namespace not found")

    with patch(
        "template_store.api.templates.resolve_namespace_filter",
        side_effect=deny,
    ):
        resp = await call(client, auth_headers, value, params)
    assert resp.status_code == 404, f"{label} {params} not gated: {resp.status_code} {resp.text}"


@pytest.mark.parametrize("label,call", SPECS, ids=_IDS)
@pytest.mark.asyncio
async def test_filter_restricts_query(label, call, client: AsyncClient, auth_headers: dict):
    """The NamespaceFilter query is merged into the Mongo query — a filter
    scoped to a namespace the template is NOT in hides it (404), proving the
    accessible-namespaces restriction reaches the database query."""
    value = f"CASE579_SCOPE_{label.upper()}"
    await _seed(client, auth_headers, value)

    async def foreign_scope(identity, namespace, required="read"):
        return NamespaceFilter(
            query={"namespace": {"$in": ["case579-other-ns"]}},
            namespaces=["case579-other-ns"],
        )

    with patch(
        "template_store.api.templates.resolve_namespace_filter",
        side_effect=foreign_scope,
    ):
        resp = await call(client, auth_headers, value, {})
    assert resp.status_code == 404, (
        f"{label}: template in wip leaked through a foreign-namespace filter: "
        f"{resp.status_code} {resp.text}"
    )
