"""template-store per-route authorization (CASE-845).

An authorized caller passes a gate whether it runs or not, so a gate is
only observable from a credential it should refuse. Every route is in ROUTES
with its access group and a real request against a real template in 'wip',
and a completeness test fails when a route exists that ROUTES does not
list: a new route cannot skip the matrix.

Keys (tests/conftest.py, WIP_AUTH_API_KEYS_JSON):
- NOGRANT  no grant anywhere;
- OTHER    read on 'xns-closed' only, nothing on 'wip';
- WRITER   write on 'wip';
- WIPADMIN admin on 'wip';
- ADMIN    the admin master key.

Groups:
- public:     answered without a key.
- ns:         addresses 'wip'; a caller who cannot read it gets 404 (its
              existence is not leaked); a writer passes.
- ns_bulk:    a bulk write addressing 'wip': HTTP 200 always, and each item
              a caller cannot reach answers not_found.
- ns_admin:   admin on 'wip' (or the privileged groups); a writer gets 403,
              a caller who cannot read 'wip' 404.
- privileged: wip-admins / wip-services only (403 for everyone else).

"Passes the gate" is asserted as "not refused" (not 401/403/404, or for a
bulk item not not_found/forbidden): a request that passes may still fail
in the handler (the template is no edge type, say).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from template_store.main import app

P = "/api/template-store"
NOGRANT = "test_scoped_key"
OTHER = "test_xns_only_key"
WRITER = "test_xns_writer_key"
WIPADMIN = "test_wip_admin_key"
ADMIN = "test_api_key"

Ctx = dict[str, str]
Req = Callable[[Ctx], tuple[str, dict[str, Any]]]
VALUE = "MATRIX_845"


def _get(path: str) -> Req:
    return lambda ctx: (path.format(**ctx), {})


def _json(path: str, body: Any) -> Req:
    def build(ctx: Ctx):
        filled = body(ctx) if callable(body) else body
        return path.format(**ctx), {"json": filled}
    return build


def _template(value: str) -> dict:
    return {"value": value, "label": value, "namespace": "wip", "identity_fields": ["name"],
            "fields": [{"name": "name", "label": "Name", "type": "string", "mandatory": True}]}


# (method, OpenAPI path template, group, request)
ROUTES: list[tuple[str, str, str, Req]] = [
    ("GET", "/", "public", _get("/")),
    ("GET", f"{P}/", "public", _get(f"{P}/")),
    ("GET", "/health", "public", _get("/health")),
    ("GET", f"{P}/health", "public", _get(f"{P}/health")),
    ("GET", "/ready", "public", _get("/ready")),
    ("GET", f"{P}/health/events", "privileged", _get(f"{P}/health/events")),
    ("GET", "/health/integrity", "privileged", _get("/health/integrity")),
    ("POST", f"{P}/templates", "ns", _json(P + "/templates", [_template("MATRIX_845_NEW")])),
    ("GET", f"{P}/templates", "ns", _get(P + "/templates?namespace=wip")),
    ("PUT", f"{P}/templates", "ns_bulk",
     _json(P + "/templates?namespace=wip", lambda c: [{"template_id": c["tpl"], "label": "changed"}])),
    ("DELETE", f"{P}/templates", "ns_bulk",
     _json(P + "/templates?namespace=wip", lambda c: [{"id": c["tpl"]}])),
    ("GET", f"{P}/templates/stamp", "ns", _get(P + "/templates/stamp?namespace=wip")),
    ("GET", f"{P}/templates/{{template_id}}", "ns", _get(P + "/templates/{tpl}")),
    ("GET", f"{P}/templates/{{template_id}}/raw", "ns", _get(P + "/templates/{tpl}/raw")),
    ("GET", f"{P}/templates/by-value/{{value}}", "ns", _get(P + f"/templates/by-value/{VALUE}?namespace=wip")),
    ("GET", f"{P}/templates/by-value/{{value}}/raw", "ns",
     _get(P + f"/templates/by-value/{VALUE}/raw?namespace=wip")),
    ("GET", f"{P}/templates/by-value/{{value}}/versions", "ns",
     _get(P + f"/templates/by-value/{VALUE}/versions?namespace=wip")),
    ("GET", f"{P}/templates/by-value/{{value}}/versions/{{version}}", "ns",
     _get(P + f"/templates/by-value/{VALUE}/versions/1?namespace=wip")),
    ("GET", f"{P}/templates/{{template_id}}/versions", "ns", _get(P + "/templates/{tpl}/versions")),
    ("GET", f"{P}/templates/{{template_id}}/dependencies", "ns", _get(P + "/templates/{tpl}/dependencies")),
    ("POST", f"{P}/templates/{{template_id}}/validate", "ns", _json(P + "/templates/{tpl}/validate", {})),
    ("POST", f"{P}/templates/{{template_id}}/activate", "ns",
     _json(P + "/templates/{tpl}/activate?namespace=wip&dry_run=true", None)),
    ("POST", f"{P}/templates/{{template_id}}/reactivate", "ns",
     _json(P + "/templates/{tpl}/reactivate?namespace=wip&version=1", None)),
    ("POST", f"{P}/templates/{{template_id}}/cascade", "ns", _json(P + "/templates/{tpl}/cascade", None)),
    ("POST", f"{P}/templates/{{template_id}}/endpoints", "ns",
     _json(P + "/templates/{tpl}/endpoints?namespace=wip", {"add_target_templates": [VALUE]})),
    ("GET", f"{P}/templates/{{template_id}}/children", "ns", _get(P + "/templates/{tpl}/children")),
    ("GET", f"{P}/templates/{{template_id}}/descendants", "ns", _get(P + "/templates/{tpl}/descendants")),
    ("POST", f"{P}/maintenance/heal-endpoints", "ns_admin",
     _json(P + "/maintenance/heal-endpoints", {"namespace": "wip", "dry_run": True})),
    ("POST", f"{P}/maintenance/reconcile-synonym-lifecycle", "ns_admin",
     _json(P + "/maintenance/reconcile-synonym-lifecycle", {"namespace": "wip", "dry_run": True})),
]

_REFUSED = (401, 403, 404)


@pytest.fixture
async def ctx(client):
    created = (await client.post(f"{P}/templates", headers={"X-API-Key": ADMIN},
                                 json=[_template(VALUE)])).json()
    item = created["results"][0]
    assert item["status"] == "created", item
    return {"tpl": item["id"]}


async def _call(route, ctx: Ctx, key: str | None):
    method, _template_path, _group, build = route
    path, kwargs = build(ctx)
    headers = {"X-API-Key": key} if key else {}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        return await c.request(method, path, **kwargs)


def _of(group: str):
    return [pytest.param(r, id=f"{r[0]} {r[1]}") for r in ROUTES if r[2] == group]


def _permitted(route, *keys: str) -> tuple[str, ...]:
    """A DELETE runs once: the first permitted key really deletes."""
    return keys[:1] if route[0] == "DELETE" else keys


def test_matrix_lists_every_route():
    """A route missing from ROUTES fails here, so the matrix stays complete."""
    in_app = {
        (method.upper(), template)
        for template, item in app.openapi()["paths"].items()
        for method in item if method in ("get", "put", "post", "delete", "patch")
    }
    in_app.add(("GET", f"{P}/"))  # a root hidden from the schema
    in_matrix = {(m, t) for m, t, _, _ in ROUTES}
    assert in_app - in_matrix == set(), "routes missing from the matrix"
    assert in_matrix - in_app == set(), "matrix rows for routes that no longer exist"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _of("public"))
async def test_public_routes(route, ctx):
    assert (await _call(route, ctx, None)).status_code not in (401, 403)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _of("ns"))
async def test_namespace_routes(route, ctx):
    assert (await _call(route, ctx, None)).status_code == 401
    for key in (NOGRANT, OTHER):
        resp = await _call(route, ctx, key)
        assert resp.status_code == 404, (key, resp.status_code, resp.text[:200])
    for key in (WRITER, ADMIN):
        resp = await _call(route, ctx, key)
        assert resp.status_code not in _REFUSED, (key, resp.status_code, resp.text[:200])


def _item_codes(resp) -> set[str | None]:
    assert resp.status_code == 200, resp.text[:200]
    return {item.get("error_code") for item in resp.json()["results"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _of("ns_bulk"))
async def test_namespace_bulk_routes(route, ctx):
    assert (await _call(route, ctx, None)).status_code == 401
    for key in (NOGRANT, OTHER):
        resp = await _call(route, ctx, key)
        assert _item_codes(resp) == {"not_found"}, (key, [i.get("error") for i in resp.json()["results"]])
    for key in _permitted(route, WRITER, ADMIN):
        assert not _item_codes(await _call(route, ctx, key)) & {"not_found", "forbidden"}, key


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _of("ns_admin"))
async def test_namespace_admin_routes(route, ctx):
    assert (await _call(route, ctx, None)).status_code == 401
    for key in (NOGRANT, OTHER):
        resp = await _call(route, ctx, key)
        assert resp.status_code == 404, (key, resp.status_code, resp.text[:200])
    resp = await _call(route, ctx, WRITER)
    assert resp.status_code == 403, (resp.status_code, resp.text[:200])
    for key in _permitted(route, WIPADMIN, ADMIN):
        resp = await _call(route, ctx, key)
        assert resp.status_code not in _REFUSED, (key, resp.status_code, resp.text[:200])


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _of("privileged"))
async def test_privileged_routes(route, ctx):
    assert (await _call(route, ctx, None)).status_code == 401
    for key in (NOGRANT, OTHER, WRITER, WIPADMIN):
        resp = await _call(route, ctx, key)
        assert resp.status_code == 403, (key, resp.status_code, resp.text[:200])
    assert (await _call(route, ctx, ADMIN)).status_code not in (401, 403)
