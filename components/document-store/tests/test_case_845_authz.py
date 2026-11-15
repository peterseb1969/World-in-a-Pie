"""document-store per-route authorization (CASE-845).

An authorized caller passes a gate whether it runs or not, so a gate is
only observable from a credential it should refuse. Every route is in ROUTES
with its access group and a real request against real data (a document, a
file and a backup job in 'wip'), and a completeness test fails when a route
exists that ROUTES does not list: a new route cannot skip the matrix.

Keys (tests/conftest.py, WIP_AUTH_API_KEYS_JSON):
- NOGRANT  no grant anywhere (scoped to a namespace no data lives in);
- OTHER    read on 'xns-closed' only, nothing on 'wip';
- WRITER   write on 'wip';
- WIPADMIN admin on 'wip';
- SERVICES wip-services (no namespace scope);
- ADMIN    wip-admins.

Groups:
- public:        answered without a key.
- authenticated: any valid key.
- ns:            addresses 'wip'; a caller who cannot read it gets 404 (its
                 existence is not leaked); a writer passes.
- ns_bulk:       a bulk write addressing 'wip': HTTP 200 always, and each
                 item a caller cannot reach answers not_found.
- ns_admin:      admin on 'wip' (or the privileged groups); a writer gets
                 403, a caller who cannot read 'wip' 404.
- privileged:    wip-admins / wip-services only (403 for everyone else).
- admin:         wip-admins only; a wip-services key gets 403 too.
- filtered:      a listing that shows a caller only what it can read.

"Passes the gate" is asserted as "not refused" (not 401/403/404, or for a
bulk item not not_found/forbidden), because a request that passes may still
fail in the handler on test infrastructure (no NATS, a mocked object store).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from document_store.main import app
from tests.test_files import _insert_file, file_client  # noqa: F401 — fixture

P = "/api/document-store"
NOGRANT = "test_scoped_key"
OTHER = "test_xns_only_key"
WRITER = "test_xns_writer_key"
WIPADMIN = "test_wip_admin_key"
SERVICES = "test_services_key"
ADMIN = "test_api_key"

Ctx = dict[str, str]
# A request: (path, keyword arguments for httpx's request()).
Req = Callable[[Ctx], tuple[str, dict[str, Any]]]


def _person(nid: str) -> dict:
    return {"template_id": "PERSON", "namespace": "wip",
            "data": {"national_id": nid, "first_name": "A", "last_name": "B"}}


def _get(path: str) -> Req:
    return lambda ctx: (path.format(**ctx), {})


def _json(path: str, body: Any) -> Req:
    def build(ctx: Ctx):
        filled = body(ctx) if callable(body) else body
        return path.format(**ctx), {"json": filled}
    return build


def _upload(path: str, name: str, content: bytes, data: dict | None = None,
            field: str = "file") -> Req:
    return lambda ctx: (path.format(**ctx), {
        "files": {field: (name, content, "text/csv" if name.endswith(".csv") else "application/zip")},
        "data": data or {},
    })


CSV = b"national_id,first_name,last_name\n845000777,A,B\n"

# (method, OpenAPI path template, group, request)
ROUTES: list[tuple[str, str, str, Req]] = [
    ("GET", "/", "public", _get("/")),
    ("GET", f"{P}/", "public", _get(f"{P}/")),
    ("GET", "/health", "public", _get("/health")),
    ("GET", f"{P}/health", "public", _get(f"{P}/health")),
    ("GET", "/ready", "public", _get("/ready")),
    ("GET", "/debug/timing", "admin", _get("/debug/timing")),
    ("POST", "/debug/timing/reset", "admin", _json("/debug/timing/reset", None)),
    ("GET", "/debug/cache", "admin", _get("/debug/cache")),
    ("POST", "/debug/cache/clear", "admin", _json("/debug/cache/clear", None)),
    ("GET", "/health/integrity", "privileged", _get("/health/integrity")),
    ("GET", f"{P}/health/events", "privileged", _get(f"{P}/health/events")),
    # documents
    ("GET", f"{P}/documents", "ns", _get(P + "/documents?namespace=wip")),
    ("POST", f"{P}/documents", "ns", _json(P + "/documents", [_person("845000002")])),
    ("PATCH", f"{P}/documents", "ns_bulk",
     _json(P + "/documents", lambda c: [{"document_id": c["doc"], "patch": {"first_name": "Z"}}])),
    ("DELETE", f"{P}/documents", "ns_bulk", _json(P + "/documents", lambda c: [{"id": c["doc"]}])),
    ("POST", f"{P}/documents/migrate", "ns",
     _json(P + "/documents/migrate?namespace=wip",
           {"template_id": "PERSON", "from_version": 1, "to_version": 1, "dry_run": True})),
    ("GET", f"{P}/documents/impact-stats", "ns",
     _get(P + "/documents/impact-stats?namespace=wip&template_id=PERSON")),
    ("GET", f"{P}/documents/template-facets", "ns",
     _get(P + "/documents/template-facets?namespace=wip&template_id=PERSON")),
    ("GET", f"{P}/documents/{{document_id}}", "ns", _get(P + "/documents/{doc}")),
    ("GET", f"{P}/documents/{{document_id}}/versions", "ns", _get(P + "/documents/{doc}/versions")),
    ("GET", f"{P}/documents/{{document_id}}/versions/{{version}}", "ns",
     _get(P + "/documents/{doc}/versions/1")),
    ("GET", f"{P}/documents/{{document_id}}/latest", "ns", _get(P + "/documents/{doc}/latest")),
    ("GET", f"{P}/documents/{{document_id}}/relationships", "ns",
     _get(P + "/documents/{doc}/relationships")),
    ("GET", f"{P}/documents/{{document_id}}/traverse", "ns", _get(P + "/documents/{doc}/traverse")),
    ("POST", f"{P}/documents/query", "ns",
     _json(P + "/documents/query?namespace=wip", {"template_id": "PERSON"})),
    ("GET", f"{P}/documents/by-identity/{{identity_hash}}", "ns",
     _get(P + "/documents/by-identity/{ihash}?namespace=wip&template_id=PERSON")),
    # validation, table view, import
    ("POST", f"{P}/validation/validate", "ns",
     _json(P + "/validation/validate",
           {"template_id": "PERSON", "namespace": "wip", "data": {"national_id": "845000009"}})),
    ("POST", f"{P}/validation/validate-bulk", "ns",
     _json(P + "/validation/validate-bulk",
           {"namespace": "wip", "template_id": "PERSON", "items": [{"national_id": "845000009"}]})),
    ("POST", f"{P}/validation/validate-candidate", "ns",
     _json(P + "/validation/validate-candidate", {
         "namespace": "wip",
         "template_definition": {
             "template_id": "candidate", "value": "CANDIDATE_845", "label": "C", "version": 1,
             "status": "active", "namespace": "wip", "identity_fields": [],
             "fields": [{"name": "f", "label": "f", "type": "string"}], "rules": [],
         },
         "documents": [{"f": "x"}],
     })),
    ("GET", f"{P}/table/{{template_id}}", "ns", _get(P + "/table/PERSON?namespace=wip")),
    ("GET", f"{P}/table/{{template_id}}/csv", "ns", _get(P + "/table/PERSON/csv?namespace=wip")),
    ("POST", f"{P}/import/preview", "authenticated", _upload(P + "/import/preview", "p.csv", CSV)),
    ("POST", f"{P}/import", "ns", _upload(P + "/import", "p.csv", CSV, {
        "template_id": "PERSON", "namespace": "wip",
        "column_mapping": '{"national_id": "national_id", "first_name": "first_name", "last_name": "last_name"}',
    })),
    # files
    ("POST", f"{P}/files", "ns", _upload(P + "/files", "f.txt", b"bytes", {"namespace": "wip"})),
    ("GET", f"{P}/files", "ns", _get(P + "/files?namespace=wip")),
    ("PATCH", f"{P}/files", "ns_bulk", _json(P + "/files", lambda c: [{"file_id": c["file"], "description": "x"}])),
    ("DELETE", f"{P}/files", "ns_bulk", _json(P + "/files", lambda c: [{"id": c["file"]}])),
    ("POST", f"{P}/files/reactivate", "ns_bulk", _json(P + "/files/reactivate", lambda c: [{"id": c["file"]}])),
    ("GET", f"{P}/files/{{file_id}}", "ns", _get(P + "/files/{file}")),
    ("GET", f"{P}/files/{{file_id}}/download", "ns", _get(P + "/files/{file}/download")),
    ("GET", f"{P}/files/{{file_id}}/content", "ns", _get(P + "/files/{file}/content")),
    ("GET", f"{P}/files/{{file_id}}/documents", "ns", _get(P + "/files/{file}/documents")),
    ("DELETE", f"{P}/files/{{file_id}}/hard", "ns_admin", _json(P + "/files/{file}/hard", None)),
    ("GET", f"{P}/files/by-checksum/{{checksum}}", "filtered", _get(P + "/files/by-checksum/{checksum}?namespace=wip")),
    ("GET", f"{P}/files/orphans/list", "privileged", _get(P + "/files/orphans/list?namespace=wip")),
    ("GET", f"{P}/files/health/integrity", "privileged", _get(P + "/files/health/integrity")),
    ("POST", f"{P}/files/storage/delete", "privileged",
     _json(P + "/files/storage/delete", {"namespace": "wip", "storage_keys": ["k"]})),
    # replay
    ("POST", f"{P}/replay/start", "ns_admin", _json(P + "/replay/start", {"filter": {"namespace": "wip"}})),
    ("GET", f"{P}/replay/{{session_id}}", "ns_admin", _get(P + "/replay/{session}")),
    ("DELETE", f"{P}/replay/{{session_id}}", "ns_admin", _json(P + "/replay/{session}", None)),
    ("POST", f"{P}/replay/{{session_id}}/pause", "ns_admin", _json(P + "/replay/{session}/pause", None)),
    ("POST", f"{P}/replay/{{session_id}}/resume", "ns_admin", _json(P + "/replay/{session}/resume", None)),
    # backup and restore
    ("POST", f"{P}/backup/namespaces/{{namespace}}/backup", "ns_admin",
     _json(P + "/backup/namespaces/wip/backup", {})),
    ("POST", f"{P}/backup/namespaces/{{namespace}}/restore", "ns_admin",
     _upload(P + "/backup/namespaces/wip/restore", "a.zip", b"PK\x05\x06" + b"\x00" * 18,
             field="archive")),
    ("POST", f"{P}/backup/namespaces/{{namespace}}/validate", "ns",
     _json(P + "/backup/namespaces/wip/validate", {})),
    ("GET", f"{P}/backup/jobs", "filtered", _get(P + "/backup/jobs?namespace=wip")),
    ("GET", f"{P}/backup/jobs/{{job_id}}", "ns", _get(P + "/backup/jobs/{job}")),
    ("DELETE", f"{P}/backup/jobs/{{job_id}}", "ns_admin", _json(P + "/backup/jobs/{job}", None)),
    ("GET", f"{P}/backup/jobs/{{job_id}}/events", "ns", _get(P + "/backup/jobs/{job}/events")),
    ("GET", f"{P}/backup/jobs/{{job_id}}/download", "ns_admin", _get(P + "/backup/jobs/{job}/download")),
    ("POST", f"{P}/backup/jobs/{{job_id}}/restore", "ns_admin",
     _json(P + "/backup/jobs/{job}/restore", {"mode": "restore", "dry_run": True})),
    ("GET", f"{P}/backup/provenance", "ns", _get(P + "/backup/provenance?namespace=wip")),
    # maintenance
    ("POST", f"{P}/maintenance/rehash-identities", "ns_admin",
     _json(P + "/maintenance/rehash-identities", {"namespace": "wip", "dry_run": True})),
    ("POST", f"{P}/maintenance/reconcile-synonym-lifecycle", "ns_admin",
     _json(P + "/maintenance/reconcile-synonym-lifecycle", {"namespace": "wip", "dry_run": True})),
]

_REFUSED = (401, 403, 404)


def _permitted(route, *keys: str) -> tuple[str, ...]:
    """The keys a route is checked to let through. A DELETE runs once: the
    first permitted key really deletes, and a second would meet not_found."""
    return keys[:1] if route[0] == "DELETE" else keys


@pytest.fixture
async def ctx(file_client):  # noqa: F811
    """A document, a file and a validation job in 'wip', and a replay
    session recorded for 'wip'."""
    admin = {"X-API-Key": ADMIN}
    created = (await file_client.post(f"{P}/documents", headers=admin, json=[_person("845000001")])).json()
    item = created["results"][0]
    checksum = "c" * 64
    f = await _insert_file(namespace="wip", checksum=checksum)
    job = (await file_client.post(f"{P}/backup/namespaces/wip/validate", headers=admin, json={})).json()

    async def chunks(_key, chunk_size=64 * 1024):
        yield b"bytes"

    import document_store.services.file_service as file_service

    file_service.get_file_storage_client().download_stream = chunks

    from document_store.models.replay import ReplayFilter, ReplaySession
    from document_store.services.replay_service import get_replay_service

    service = get_replay_service()
    service._sessions["replay-845"] = ReplaySession(
        session_id="replay-845", filter=ReplayFilter(namespace="wip"),
        stream_name="s", subject_prefix="p",
    )
    yield {"doc": item["document_id"], "ihash": item["identity_hash"], "file": f.file_id,
           "checksum": checksum, "job": job["job_id"], "session": "replay-845"}
    service._sessions.pop("replay-845", None)


async def _call(route: tuple[str, str, str, Req], ctx: Ctx, key: str | None):
    method, _template, _group, build = route
    path, kwargs = build(ctx)
    headers = {"X-API-Key": key} if key else {}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        return await c.request(method, path, **kwargs)


def _of(group: str):
    return [pytest.param(r, id=f"{r[0]} {r[1]}") for r in ROUTES if r[2] == group]


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
@pytest.mark.parametrize("route", _of("authenticated"))
async def test_authenticated_routes(route, ctx):
    assert (await _call(route, ctx, None)).status_code == 401
    for key in (NOGRANT, ADMIN):
        assert (await _call(route, ctx, key)).status_code not in (401, 403), key


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
        assert _item_codes(await _call(route, ctx, key)) == {"not_found"}, key
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


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _of("admin"))
async def test_admin_routes(route, ctx):
    assert (await _call(route, ctx, None)).status_code == 401
    for key in (NOGRANT, OTHER, WRITER, WIPADMIN, SERVICES):
        resp = await _call(route, ctx, key)
        assert resp.status_code == 403, (key, resp.status_code, resp.text[:200])
    assert (await _call(route, ctx, ADMIN)).status_code not in (401, 403)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _of("filtered"))
async def test_filtered_listings(route, ctx):
    assert (await _call(route, ctx, None)).status_code == 401
    for key in (NOGRANT, OTHER):
        resp = await _call(route, ctx, key)
        assert resp.status_code == 200 and resp.json() == [], (key, resp.text[:200])
    resp = await _call(route, ctx, WRITER)
    assert resp.status_code == 200 and resp.json(), resp.text[:200]
