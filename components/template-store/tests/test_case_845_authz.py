"""template-store per-route authorization (CASE-845).

An authorized caller passes a gate whether it runs or not, so a gate is
only observable from a credential it should refuse. Every route is in ROUTES
with its access group and a real request against a real template in 'wip',
and a completeness test fails when a route exists that ROUTES does not
list: a new route cannot skip the matrix.

Keys (tests/conftest.py, WIP_AUTH_API_KEYS_JSON):
- NOGRANT  no grant anywhere;
- OTHER    read on 'xns-closed' only, nothing on 'wip';
- READER   read on 'wip' only;
- XWRITER  write on 'wip', read on 'xns-closed';
- WRITER   write on 'wip';
- WIPADMIN admin on 'wip';
- SERVICES wip-services (no namespace scope);
- ADMIN    the admin master key.

Groups:
- public:     answered without a key.
- ns:         a read addressing 'wip'; a caller who cannot read it gets 404
              (its existence is not leaked); a reader passes.
- ns_write:   a write addressing 'wip': as ns, and a reader gets 403, so a
              gate weakened to "read" fails here.
- ns_bulk_write: a bulk write addressing 'wip': HTTP 200 always; each item a
              caller cannot read answers not_found, and forbidden for a
              reader.
- ns_admin:   admin on 'wip' (or the privileged groups); a reader or a
              writer gets 403, a caller who cannot read 'wip' 404.

A write that names a template is also sent for a template living in
'xns-closed' while naming ?namespace=wip, by a key that writes 'wip' but only
reads 'xns-closed': the gate must be on the template's own namespace, not on
the parameter.
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
from wip_auth.testing import api_route_methods

P = "/api/template-store"
NOGRANT = "test_scoped_key"
OTHER = "test_xns_only_key"
READER = "test_wip_reader_key"
XWRITER = "test_xns_reader_key"
WRITER = "test_xns_writer_key"
WIPADMIN = "test_wip_admin_key"
SERVICES = "test_services_key"
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
    ("GET", "/livez", "public", _get("/livez")),
    ("GET", f"{P}/health/events", "privileged", _get(f"{P}/health/events")),
    ("GET", "/health/integrity", "privileged", _get("/health/integrity")),
    ("POST", f"{P}/templates", "ns_write", _json(P + "/templates", [_template("MATRIX_845_NEW")])),
    ("GET", f"{P}/templates", "ns", _get(P + "/templates?namespace=wip")),
    ("PUT", f"{P}/templates", "ns_bulk_write",
     _json(P + "/templates?namespace=wip", lambda c: [{"template_id": c["tpl"], "label": "changed"}])),
    ("DELETE", f"{P}/templates", "ns_bulk_write",
     _json(P + "/templates?namespace=wip", lambda c: [{"id": c["tpl"], "version": 1}])),
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
    ("POST", f"{P}/templates/{{template_id}}/activate", "ns_write",
     _json(P + "/templates/{tpl}/activate?namespace=wip&dry_run=true", None)),
    ("POST", f"{P}/templates/{{template_id}}/reactivate", "ns_write",
     _json(P + "/templates/{tpl}/reactivate?namespace=wip&version=1", None)),
    ("POST", f"{P}/templates/{{template_id}}/cascade", "ns_write", _json(P + "/templates/{tpl}/cascade", None)),
    ("POST", f"{P}/templates/{{template_id}}/endpoints", "ns_write",
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
    other = (await client.post(f"{P}/templates", headers={"X-API-Key": ADMIN},
                               json=[{**_template(VALUE), "namespace": "xns-closed"}])).json()
    assert other["results"][0]["status"] == "created", other
    return {"tpl": item["id"], "xtpl": other["results"][0]["id"]}


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
    """A route missing from ROUTES fails here, so the matrix stays complete.
    Schema-hidden routes (/livez, the service root) count too: a route kept
    out of the OpenAPI schema does not skip the matrix."""
    in_app = api_route_methods(app)
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
    for key in (READER, WRITER, ADMIN):
        resp = await _call(route, ctx, key)
        assert resp.status_code not in _REFUSED, (key, resp.status_code, resp.text[:200])


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _of("ns_write"))
async def test_namespace_write_routes(route, ctx):
    assert (await _call(route, ctx, None)).status_code == 401
    for key in (NOGRANT, OTHER):
        resp = await _call(route, ctx, key)
        assert resp.status_code == 404, (route[1], key, resp.status_code, resp.text[:200])
    resp = await _call(route, ctx, READER)
    assert resp.status_code == 403, (route[1], resp.status_code, resp.text[:200])
    for key in _permitted(route, WRITER, ADMIN):
        resp = await _call(route, ctx, key)
        assert resp.status_code not in _REFUSED, (key, resp.status_code, resp.text[:200])


def _item_codes(resp) -> set[str | None]:
    assert resp.status_code == 200, resp.text[:200]
    return {item.get("error_code") for item in resp.json()["results"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _of("ns_bulk_write"))
async def test_namespace_bulk_write_routes(route, ctx):
    assert (await _call(route, ctx, None)).status_code == 401
    for key in (NOGRANT, OTHER):
        resp = await _call(route, ctx, key)
        assert _item_codes(resp) == {"not_found"}, (key, [i.get("error") for i in resp.json()["results"]])
    assert _item_codes(await _call(route, ctx, READER)) == {"forbidden"}, route[1]
    for key in _permitted(route, WRITER, ADMIN):
        assert not _item_codes(await _call(route, ctx, key)) & {"not_found", "forbidden"}, key


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _of("ns_admin"))
async def test_namespace_admin_routes(route, ctx):
    assert (await _call(route, ctx, None)).status_code == 401
    for key in (NOGRANT, OTHER):
        resp = await _call(route, ctx, key)
        assert resp.status_code == 404, (key, resp.status_code, resp.text[:200])
    for key in (READER, WRITER):
        resp = await _call(route, ctx, key)
        assert resp.status_code == 403, (route[1], key, resp.status_code, resp.text[:200])
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
    for key in (SERVICES, ADMIN):
        resp = await _call(route, ctx, key)
        assert resp.status_code not in (401, 403), (key, resp.status_code, resp.text[:200])


_BY_TEMPLATE = [
    pytest.param(r, id=f"{r[0]} {r[1]}") for r in ROUTES
    if r[2] == "ns_bulk_write" or (r[2] == "ns_write" and "{template_id}" in r[1])
]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _BY_TEMPLATE)
async def test_a_write_is_gated_on_the_templates_own_namespace(route, ctx):
    """The template lives in 'xns-closed'; the request names ?namespace=wip,
    where the caller may write. A gate on the parameter would let it through;
    the gate on the template's own namespace refuses it (the caller only
    reads 'xns-closed', so the template is visible and the answer is 403)."""
    elsewhere = {**ctx, "tpl": ctx["xtpl"]}
    resp = await _call(route, elsewhere, XWRITER)
    if route[2] == "ns_bulk_write":
        assert _item_codes(resp) == {"forbidden"}, (route[1], resp.text[:300])
    else:
        assert resp.status_code == 403, (route[1], resp.status_code, resp.text[:200])
