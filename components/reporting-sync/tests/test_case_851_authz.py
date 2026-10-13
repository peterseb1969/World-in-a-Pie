"""reporting-sync per-route authorization (CASE-851, Phase 2b C4).

Every route is in ROUTES with its access group, and a completeness test
fails when a route exists that the table does not list — a new route
cannot skip the matrix.

Groups:
- public:        probes, answered without a key.
- privileged:    operator and raw-SQL routes; wip-admins / wip-services only.
- ns_read:       routes addressing one namespace; read on it required,
                 and an invisible namespace answers 404 (existence is not
                 leaked).
- ns_admin:      view writes; ADMIN on the namespace or the privileged
                 groups (read/write grants answer 403, invisible 404).
- authenticated: any valid key (/status, which filters what it shows).
- scoped:        listings that span namespaces. They run in the caller's
                 effective namespace (C5): an explicit namespace must be
                 readable (404); a key that reads one namespace gets it
                 implicitly; a key that reads several must name one
                 (400); a key that reads none is refused (403); an
                 unrestricted caller keeps the all-namespaces listing.

No databases run in these tests: a request that passes its gate reaches
the handler and fails there (503, 422 or 404 for a dummy id), which is
why "passed the gate" is asserted as "not refused" rather than as 200.
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient

from reporting_sync.main import app
from wip_auth import clear_permission_cache

ADMIN = os.environ["WIP_AUTH_LEGACY_API_KEY"]
READER = "rs_reader_key"  # read on ns-a
WRITER = "rs_writer_key"  # write on ns-a
NSADMIN = "rs_nsadmin_key"  # admin on ns-a
SERVICE = "rs_service_key"  # wip-services, no namespace grants
NOTHING = "rs_nothing_key"  # read on ns-z only
MULTI = "rs_multi_key"  # read on ns-a and ns-c

P = "/api/reporting-sync"

# (method, path template, group, concrete path for namespace ns-a or None)
ROUTES = [
    ("GET", "/health", "public", None),
    ("GET", f"{P}/health", "public", None),
    ("GET", f"{P}/", "public", None),
    ("GET", f"{P}/status", "authenticated", None),
    ("GET", f"{P}/parity", "ns_read", f"{P}/parity?namespace={{ns}}"),
    ("GET", f"{P}/metrics", "privileged", None),
    ("GET", f"{P}/metrics/consumer", "privileged", None),
    ("GET", f"{P}/alerts", "privileged", None),
    ("PUT", f"{P}/alerts/config", "privileged", None),
    ("POST", f"{P}/alerts/test", "privileged", None),
    ("GET", f"{P}/table-name", "ns_read",
     f"{P}/table-name?namespace={{ns}}&template_value=PATIENT"),
    ("GET", f"{P}/schema/{{template_value}}", "ns_read",
     f"{P}/schema/PATIENT?namespace={{ns}}"),
    ("POST", f"{P}/sync/batch/terminologies", "privileged", None),
    ("POST", f"{P}/sync/batch/terms", "privileged", None),
    ("POST", f"{P}/sync/batch/term_relations", "privileged", None),
    ("POST", f"{P}/sync/batch/templates", "privileged", None),
    ("POST", f"{P}/sync/batch", "privileged", None),
    ("GET", f"{P}/sync/batch/jobs", "privileged", None),
    ("GET", f"{P}/sync/batch/jobs/{{job_id}}", "privileged", None),
    ("POST", f"{P}/sync/batch/{{template_value}}", "privileged", None),
    ("DELETE", f"{P}/sync/batch/jobs/{{job_id}}", "privileged", None),
    ("DELETE", f"{P}/sync/batch/jobs", "privileged", None),
    ("GET", f"{P}/health/integrity", "privileged", None),
    ("POST", f"{P}/search", "scoped", None),
    ("GET", f"{P}/activity/recent", "scoped", None),
    ("GET", f"{P}/references/term/{{term_id}}/documents", "scoped", None),
    ("GET", f"{P}/entity/{{entity_type}}/{{entity_id}}/references", "entity", None),
    ("GET", f"{P}/entity/{{entity_type}}/{{entity_id}}/referenced-by", "scoped", None),
    ("GET", f"{P}/tables", "scoped", None),
    ("POST", f"{P}/query", "privileged", None),
    ("GET", f"{P}/export/csv", "ns_read",
     f"{P}/export/csv?namespace={{ns}}&table=doc_patient"),
    ("POST", f"{P}/export/csv", "privileged", None),
    ("DELETE", f"{P}/namespace/{{prefix}}", "privileged", None),
    ("POST", f"{P}/namespace/{{prefix}}/views", "ns_admin", f"{P}/namespace/{{ns}}/views"),
    ("GET", f"{P}/namespace/{{prefix}}/views", "ns_read", f"{P}/namespace/{{ns}}/views"),
    ("POST", f"{P}/namespace/{{prefix}}/views/{{view_name}}/rows", "ns_read",
     f"{P}/namespace/{{ns}}/views/v_x/rows"),
    ("DELETE", f"{P}/namespace/{{prefix}}/views/{{view_name}}", "ns_admin",
     f"{P}/namespace/{{ns}}/views/v_x"),
]


def _concrete(template: str) -> str:
    """Fill path parameters with dummy values (namespace params get ns-a)."""
    return (
        template.replace("{prefix}", "ns-a")
        .replace("{job_id}", "job-1")
        .replace("{template_value}", "PATIENT")
        .replace("{term_id}", "term-1")
        .replace("{entity_type}", "documents")
        .replace("{entity_id}", "doc-1")
        .replace("{view_name}", "v_x")
    )


# Routes whose handler only runs with a valid body; without one, request
# validation answers 422 before any namespace logic is reached.
_BODIES = {
    ("POST", f"{P}/search"): {"query": "x"},
    ("POST", f"{P}/namespace/ns-a/views"): {"name": "v_x", "sql": "SELECT 1"},
    ("POST", f"{P}/namespace/ns-b/views"): {"name": "v_x", "sql": "SELECT 1"},
}


async def _call(method: str, path: str, key: str | None) -> int:
    headers = {"X-API-Key": key} if key else {}
    body = _BODIES.get((method, path.split("?")[0]))
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        return (await c.request(method, path, json=body)).status_code


@pytest.fixture(autouse=True)
def _fresh_permission_cache():
    clear_permission_cache()
    yield
    clear_permission_cache()


def _of(group: str):
    return [r for r in ROUTES if r[2] == group]


def test_matrix_lists_every_route():
    """A route missing from ROUTES fails here, so the matrix stays complete."""
    in_app = set()
    for template, item in app.openapi()["paths"].items():
        for method in item:
            if method in ("get", "put", "post", "delete", "patch"):
                in_app.add((method.upper(), template))
    in_app.add(("GET", f"{P}/"))  # hidden-from-schema roots are not listed there
    in_matrix = {(m, t) for m, t, _, _ in ROUTES}
    assert in_app - in_matrix == set(), "routes missing from the matrix"
    assert in_matrix - in_app == set(), "matrix rows for routes that no longer exist"


@pytest.mark.asyncio
@pytest.mark.parametrize("method,template,group,_", _of("privileged"))
async def test_privileged_routes(method, template, group, _):
    path = _concrete(template)
    assert await _call(method, path, None) == 401
    for key in (READER, WRITER, NOTHING):
        assert await _call(method, path, key) == 403, key
    for key in (SERVICE, ADMIN):
        assert await _call(method, path, key) not in (401, 403), key


@pytest.mark.asyncio
@pytest.mark.parametrize("method,template,group,ns_path", _of("ns_read"))
async def test_namespace_read_routes(method, template, group, ns_path):
    own = ns_path.format(ns="ns-a")
    other = ns_path.format(ns="ns-b")
    assert await _call(method, own, None) == 401
    # read on ns-a passes the gate (and then fails on missing infrastructure)
    for key in (READER, WRITER, ADMIN):
        assert await _call(method, own, key) not in (401, 403, 404), key
    # a namespace the caller cannot see is reported as not found
    assert await _call(method, other, READER) == 404
    assert await _call(method, own, NOTHING) == 404
    # a service key without grants has no namespace access
    assert await _call(method, own, SERVICE) == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("method,template,group,ns_path", _of("ns_admin"))
async def test_namespace_admin_routes(method, template, group, ns_path):
    """View writes: ADMIN on the namespace, or the privileged groups."""
    own = ns_path.format(ns="ns-a")
    other = ns_path.format(ns="ns-b")
    assert await _call(method, own, None) == 401
    # read or write on the namespace is not enough for a view write
    for key in (READER, WRITER):
        assert await _call(method, own, key) == 403, key
    # invisible namespaces stay invisible, also to a key that admins another
    assert await _call(method, other, NSADMIN) == 404
    assert await _call(method, own, NOTHING) == 404
    # namespace admin and the privileged groups pass the gate
    for key in (NSADMIN, SERVICE, ADMIN):
        assert await _call(method, own, key) not in (401, 403, 404), key


@pytest.mark.asyncio
@pytest.mark.parametrize("method,template,group,_", _of("authenticated"))
async def test_authenticated_routes(method, template, group, _):
    path = _concrete(template)
    assert await _call(method, path, None) == 401
    for key in (READER, SERVICE, ADMIN):
        assert await _call(method, path, key) not in (401, 403), key


@pytest.mark.asyncio
@pytest.mark.parametrize("method,template,group,_", _of("scoped"))
async def test_scoped_listings(method, template, group, _):
    path = _concrete(template)
    joiner = "&" if "?" in path else "?"
    assert await _call(method, path, None) == 401
    # one readable namespace: used implicitly, the gate passes
    assert await _call(method, path, READER) not in (400, 401, 403, 404)
    # several readable namespaces: the caller must name one
    assert await _call(method, path, MULTI) == 400
    assert await _call(method, f"{path}{joiner}namespace=ns-c", MULTI) not in (
        400, 401, 403, 404,
    )
    # an explicit namespace the caller cannot read is not found
    assert await _call(method, f"{path}{joiner}namespace=ns-b", READER) == 404
    # a key that reads no namespace at all is refused
    assert await _call(method, path, SERVICE) == 403
    # unrestricted callers keep the all-namespaces listing
    assert await _call(method, path, ADMIN) not in (400, 401, 403, 404)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,template,group,_", _of("public"))
async def test_public_routes(method, template, group, _):
    assert await _call(method, template, None) == 200


@pytest.mark.asyncio
async def test_writer_cannot_register_views():
    """View SQL is raw SQL: namespace write is not enough to register one."""
    path = f"{P}/namespace/ns-a/views"
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            path,
            headers={"X-API-Key": WRITER},
            json={"name": "v_x", "sql": "SELECT 1"},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_status_hides_drops_outside_the_callers_namespaces(monkeypatch):
    from datetime import UTC, datetime

    from reporting_sync.main import state
    from reporting_sync.models import DroppedEvent

    def drop(ns):
        return DroppedEvent(
            event_type="document.created", entity_id=f"doc-{ns}", namespace=ns,
            error="x", deliveries=5, dropped_at=datetime.now(UTC),
        )

    monkeypatch.setattr(
        state.sync_status, "recent_drops", [drop("ns-a"), drop("ns-b"), drop(None)]
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        reader = (await c.get(f"{P}/status", headers={"X-API-Key": READER})).json()
        admin = (await c.get(f"{P}/status", headers={"X-API-Key": ADMIN})).json()
    assert [d["namespace"] for d in reader["recent_drops"]] == ["ns-a"]
    assert len(admin["recent_drops"]) == 3
    # the shared status object is not mutated by a filtered response
    assert len(state.sync_status.recent_drops) == 3


# ---------------------------------------------------------------------------
# Entity references: authorized on the namespace the entity lives in
# ---------------------------------------------------------------------------


class _FakeSearchService:
    """Returns an entity in a chosen namespace without any upstream calls."""

    def __init__(self, namespace):
        self.namespace = namespace

    async def get_entity_references(self, entity_type, entity_id):
        from reporting_sync.search_service import EntityDetails, EntityReferencesResponse

        return EntityReferencesResponse(entity=EntityDetails(
            entity_type=entity_type, entity_id=entity_id, namespace=self.namespace,
        ))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "owner,key,expected",
    [
        ("ns-a", READER, 200),  # entity in the caller's namespace
        ("ns-b", READER, 404),  # another namespace: not found, not forbidden
        (None, READER, 404),  # unknown namespace: only unrestricted callers see it
        ("ns-b", ADMIN, 200),
        (None, ADMIN, 200),
        ("ns-a", None, 401),
    ],
)
async def test_entity_references_check_the_entitys_namespace(monkeypatch, owner, key, expected):
    from reporting_sync.main import state

    monkeypatch.setattr(state, "search_service", _FakeSearchService(owner))
    status = await _call("GET", f"{P}/entity/document/doc-1/references", key)
    assert status == expected
