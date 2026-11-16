"""def-store per-route authorization (CASE-845).

An authorized caller passes a gate whether it runs or not, so a gate is
only observable from a credential it should refuse. Every route is in ROUTES
with its access group and a real request against real data in 'wip' (a
terminology, two terms and a relation between them), and a completeness
test fails when a route exists that ROUTES does not list: a new route cannot
skip the matrix.

Keys (tests/conftest.py, WIP_AUTH_API_KEYS_JSON):
- NOGRANT  no grant anywhere;
- OTHER    read on 'xns-closed' only, nothing on 'wip';
- WRITER   write on 'wip';
- WIPADMIN admin on 'wip';
- SERVICES wip-services (no namespace scope);
- ADMIN    the admin master key (wip-admins).

Groups:
- public:     answered without a key.
- ns:         addresses 'wip'; a caller who cannot read it gets 404 (its
              existence is not leaked); a writer passes.
- ns_bulk:    a bulk write addressing 'wip': HTTP 200 always, and each item
              a caller cannot reach answers not_found.
- ns_item:    one answer in a 200 body: error_code not_found for a caller
              who cannot read 'wip'.
- ns_admin:   admin on 'wip' (or the privileged groups); a writer gets 403,
              a caller who cannot read 'wip' 404.
- privileged: wip-admins / wip-services only (403 for everyone else).
- admin:      wip-admins only; a wip-services key gets 403 too.
- filtered:   a listing that shows a caller only what it can read.

"Passes the gate" is asserted as "not refused" (not 401/403/404, or for a
bulk item not not_found/forbidden): a request that passes may still fail
in the handler.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from def_store.main import app
from wip_auth.testing import api_route_methods

P = "/api/def-store"
NOGRANT = "test_scoped_key"
OTHER = "test_xns_only_key"
WRITER = "test_xns_writer_key"
WIPADMIN = "test_wip_admin_key"
SERVICES = "test_services_key"
ADMIN = "test_api_key"
TL = "MATRIX_845"

Ctx = dict[str, str]
Req = Callable[[Ctx], tuple[str, dict[str, Any]]]


def _get(path: str) -> Req:
    return lambda ctx: (path.format(**ctx), {})


def _json(path: str, body: Any) -> Req:
    def build(ctx: Ctx):
        filled = body(ctx) if callable(body) else body
        return path.format(**ctx), {"json": filled}
    return build


OBO = {"graphs": [{"id": "g", "nodes": [], "edges": []}]}
EXPORT = {"terminology": {"value": "MATRIX_845_IMPORTED", "label": "Imported"},
          "terms": [{"value": "x", "label": "x"}]}

# (method, OpenAPI path template, group, request)
ROUTES: list[tuple[str, str, str, Req]] = [
    ("GET", "/", "public", _get("/")),
    ("GET", f"{P}/", "public", _get(f"{P}/")),
    ("GET", "/health", "public", _get("/health")),
    ("GET", f"{P}/health", "public", _get(f"{P}/health")),
    ("GET", "/ready", "public", _get("/ready")),
    ("GET", "/livez", "public", _get("/livez")),
    ("GET", f"{P}/health/events", "privileged", _get(f"{P}/health/events")),
    # terminologies
    ("POST", f"{P}/terminologies", "ns",
     _json(P + "/terminologies", [{"value": "MATRIX_845_NEW", "label": "New", "namespace": "wip"}])),
    ("GET", f"{P}/terminologies", "ns", _get(P + "/terminologies?namespace=wip")),
    ("PUT", f"{P}/terminologies", "ns_bulk",
     _json(P + "/terminologies?namespace=wip", lambda c: [{"terminology_id": c["tl"], "label": "changed"}])),
    ("DELETE", f"{P}/terminologies", "ns_bulk",
     _json(P + "/terminologies?namespace=wip", lambda c: [{"id": c["tl"]}])),
    ("GET", f"{P}/terminologies/by-value/{{value}}", "ns", _get(P + f"/terminologies/by-value/{TL}?namespace=wip")),
    ("GET", f"{P}/terminologies/{{terminology_id}}", "ns", _get(P + "/terminologies/{tl}")),
    ("GET", f"{P}/terminologies/{{terminology_id}}/dependencies", "ns",
     _get(P + "/terminologies/{tl}/dependencies")),
    ("POST", f"{P}/terminologies/{{terminology_id}}/restore", "ns",
     _json(P + "/terminologies/{tl}/restore", None)),
    ("POST", f"{P}/terminologies/{{terminology_id}}/terms", "ns",
     _json(P + "/terminologies/{tl}/terms", [{"value": "third", "label": "Third"}])),
    ("GET", f"{P}/terminologies/{{terminology_id}}/terms", "ns",
     _get(P + "/terminologies/{tl}/terms?namespace=wip")),
    ("POST", f"{P}/terminologies/{{terminology_id}}/reconcile", "ns",
     _json(P + "/terminologies/{tl}/reconcile",
           {"terms": [{"value": "first", "label": "First"}], "dry_run": True})),
    # terms
    ("GET", f"{P}/terms/search", "ns", _get(P + f"/terms/search?namespace=wip&q=fir&terminology={TL}")),
    ("GET", f"{P}/terms/{{term_id}}", "ns", _get(P + "/terms/{t1}")),
    ("PUT", f"{P}/terms", "ns_bulk", _json(P + "/terms", lambda c: [{"term_id": c["t1"], "label": "changed"}])),
    ("DELETE", f"{P}/terms", "ns_bulk", _json(P + "/terms", lambda c: [{"id": c["t1"]}])),
    ("POST", f"{P}/terms/deprecate", "ns_bulk",
     _json(P + "/terms/deprecate", lambda c: [{"term_id": c["t1"], "reason": "x"}])),
    ("POST", f"{P}/terms/merge", "ns_bulk",
     _json(P + "/terms/merge", lambda c: [{"survivor_term_id": c["t1"], "absorbed_term_id": c["t2"]}])),
    # validation
    # Neither validate door permission-checks its namespace parameter: it only
    # says where the terminology identifier resolves, and read is checked
    # against the terminology's own namespace.
    # Every refusal stays in the 200 answer: a terminology the caller cannot
    # read answers as one that names nothing (error_code not_found).
    ("POST", f"{P}/validate", "ns_item",
     _json(P + "/validate?namespace=wip", {"terminology_id": TL, "value": "first"})),
    # Each item names its terminology and is read-checked against that
    # terminology's namespace, so a refusal is per item.
    ("POST", f"{P}/validate/bulk", "ns_bulk",
     _json(P + "/validate/bulk?namespace=wip", {"items": [{"terminology_id": TL, "value": "first"}]})),
    # import / export
    ("GET", f"{P}/import-export/export/{{terminology_id}}", "ns", _get(P + "/import-export/export/{tl}")),
    ("GET", f"{P}/import-export/export", "filtered", _get(P + "/import-export/export?namespace=wip")),
    ("POST", f"{P}/import-export/import", "ns",
     _json(P + "/import-export/import?namespace=wip&format=json", EXPORT)),
    ("POST", f"{P}/import-export/import-ontology", "ns",
     _json(P + f"/import-export/import-ontology?namespace=wip&terminology_value={TL}_OBO", OBO)),
    # Fetches a URL server-side, so what it brings cannot be checked against
    # any namespace before it is fetched: wip-admins only (is_superadmin). A
    # namespace the caller names is still write-checked.
    ("POST", f"{P}/import-export/import/url", "admin",
     _json(P + "/import-export/import/url?namespace=wip&url=http://127.0.0.1:9/none.json", None)),
    # audit
    ("GET", f"{P}/audit/terms/{{term_id}}", "ns", _get(P + "/audit/terms/{t1}")),
    ("GET", f"{P}/audit/terminologies/{{terminology_id}}", "ns", _get(P + "/audit/terminologies/{tl}")),
    ("GET", f"{P}/audit", "filtered", _get(P + "/audit?namespace=wip")),
    # ontology
    ("POST", f"{P}/ontology/term-relations", "ns",
     _json(P + "/ontology/term-relations?namespace=wip",
           lambda c: [{"source_term_id": c["t2"], "target_term_id": c["t1"], "relation_type": "related_to"}])),
    ("GET", f"{P}/ontology/term-relations", "ns", _get(P + "/ontology/term-relations?namespace=wip&term_id={t1}")),
    ("DELETE", f"{P}/ontology/term-relations", "ns",
     _json(P + "/ontology/term-relations?namespace=wip",
           lambda c: [{"source_term_id": c["t1"], "target_term_id": c["t2"], "relation_type": "is_a"}])),
    ("GET", f"{P}/ontology/term-relations/all", "ns", _get(P + "/ontology/term-relations/all?namespace=wip")),
    ("GET", f"{P}/ontology/terms/{{term_id}}/ancestors", "ns", _get(P + "/ontology/terms/{t1}/ancestors?namespace=wip")),
    ("GET", f"{P}/ontology/terms/{{term_id}}/descendants", "ns", _get(P + "/ontology/terms/{t1}/descendants?namespace=wip")),
    ("GET", f"{P}/ontology/terms/{{term_id}}/parents", "ns", _get(P + "/ontology/terms/{t1}/parents?namespace=wip")),
    ("GET", f"{P}/ontology/terms/{{term_id}}/children", "ns", _get(P + "/ontology/terms/{t1}/children?namespace=wip")),
    # maintenance
    ("POST", f"{P}/maintenance/registry-ghosts", "ns_admin",
     _json(P + "/maintenance/registry-ghosts?namespace=wip&dry_run=true", None)),
    ("POST", f"{P}/maintenance/reconcile-synonym-lifecycle", "ns_admin",
     _json(P + "/maintenance/reconcile-synonym-lifecycle", {"namespace": "wip", "dry_run": True})),
]

_REFUSED = (401, 403, 404)


@pytest.fixture
async def ctx(client):
    admin = {"X-API-Key": ADMIN}
    tl = (await client.post(f"{P}/terminologies", headers=admin,
                            json=[{"value": TL, "label": "Matrix", "namespace": "wip"}])).json()
    tl_id = tl["results"][0]["id"]
    terms = (await client.post(f"{P}/terminologies/{tl_id}/terms", headers=admin, json=[
        {"value": "first", "label": "First"}, {"value": "second", "label": "Second"},
    ])).json()
    t1, t2 = (r["id"] for r in terms["results"])
    rel = (await client.post(f"{P}/ontology/term-relations?namespace=wip", headers=admin, json=[
        {"source_term_id": t1, "target_term_id": t2, "relation_type": "is_a"},
    ])).json()
    assert rel["results"][0]["status"] == "created", rel
    return {"tl": tl_id, "t1": t1, "t2": t2}


async def _call(route, ctx: Ctx, key: str | None):
    method, _template, _group, build = route
    path, kwargs = build(ctx)
    headers = {"X-API-Key": key} if key else {}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        return await c.request(method, path, **kwargs)


def _of(group: str):
    return [pytest.param(r, id=f"{r[0]} {r[1]}") for r in ROUTES if r[2] == group]


def _permitted(route, *keys: str) -> tuple[str, ...]:
    """A write that changes what it addresses for good (a DELETE, a merge)
    runs once: the first permitted key really does it."""
    once = route[0] == "DELETE" or route[1].endswith(("/merge", "/deprecate"))
    return keys[:1] if once else keys


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
        assert resp.status_code == 404, (route[1], key, resp.status_code, resp.text[:200])
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
        resp = await _call(route, ctx, key)
        assert not _item_codes(resp) & {"not_found", "forbidden"}, (key, resp.text[:300])


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _of("ns_admin"))
async def test_namespace_admin_routes(route, ctx):
    assert (await _call(route, ctx, None)).status_code == 401
    for key in (NOGRANT, OTHER):
        resp = await _call(route, ctx, key)
        assert resp.status_code == 404, (route[1], key, resp.status_code, resp.text[:200])
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
    for key in (SERVICES, ADMIN):
        resp = await _call(route, ctx, key)
        assert resp.status_code not in (401, 403), (key, resp.status_code, resp.text[:200])


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _of("admin"))
async def test_admin_routes(route, ctx):
    assert (await _call(route, ctx, None)).status_code == 401
    for key in (NOGRANT, OTHER, WRITER, WIPADMIN, SERVICES):
        resp = await _call(route, ctx, key)
        assert resp.status_code == 403, (key, resp.status_code, resp.text[:200])
    assert (await _call(route, ctx, ADMIN)).status_code not in (401, 403)


def _listed(resp) -> list:
    """The entries a filtered listing returned (its one list field)."""
    assert resp.status_code == 200, resp.text[:200]
    body = resp.json()
    [entries] = [v for v in body.values() if isinstance(v, list)]
    return entries


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _of("filtered"))
async def test_filtered_listings(route, ctx):
    assert (await _call(route, ctx, None)).status_code == 401
    for key in (NOGRANT, OTHER):
        assert _listed(await _call(route, ctx, key)) == [], key
    assert _listed(await _call(route, ctx, WRITER)), "a reader of 'wip' sees its entries"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _of("ns_item"))
async def test_namespace_item_routes(route, ctx):
    assert (await _call(route, ctx, None)).status_code == 401
    for key in (NOGRANT, OTHER):
        resp = await _call(route, ctx, key)
        assert resp.status_code == 200 and resp.json().get("error_code") == "not_found", (
            key, resp.text[:200])
    for key in (WRITER, ADMIN):
        resp = await _call(route, ctx, key)
        assert resp.status_code == 200, (key, resp.text[:200])
        assert resp.json().get("error_code") not in ("not_found", "forbidden"), (key, resp.text[:200])
