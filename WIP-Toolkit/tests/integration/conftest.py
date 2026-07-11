"""Integration harness: the four WIP services mounted in-process.

The toolkit is an HTTP orchestrator — its correctness IS its contracts with
registry, def-store, template-store, and document-store. The unit suite
mocks every one of those seams, so a wrong belief about a contract passes
unit tests and ships (the entire CASE-660/665/666 tree was seam failures).
This harness mounts the REAL service apps in one process and routes every
HTTP call — the toolkit's own and the services' calls to each other —
through in-process ASGI transports. Production singletons, production
clients, production wire shapes; only the network is removed.

How the wiring works:

- Service apps are imported with test env vars set (same recipe as each
  component's own conftest). Their FastAPI lifespans never run (ASGI
  transport doesn't run lifespan), so NATS/MinIO/key-sync stay off —
  exactly like the component test suites. Mongo is initialized manually
  with ONE init_beanie over all four services' models on one test DB
  (the components' sanctioned pattern: repeated init_beanie calls rebind
  models to the wrong DB; collection names are distinct).

- Cross-service calls: every service builds `httpx.AsyncClient` per call
  with base URLs from env (http://registry, http://def-store, ...). The
  harness swaps `httpx.AsyncClient` for a subclass whose default transport
  routes by hostname to the right in-process app — one patch wires every
  internal client with zero service-code changes and zero mocks.

- The toolkit's own WIPClient is synchronous, so a bridge transport carries
  its requests onto a dedicated anyio portal thread whose event loop also
  owns the motor client and runs the apps. Everything async happens on
  that one loop — motor is loop-bound, so this is load-bearing, not style.

Requires MongoDB on localhost:27017 (wip-test.sh provisions test-mongo)
and the four services' requirements installed in the venv (the standard
dev venv has them; CI installs them in the toolkit job). Missing either →
the tests SKIP with the recipe, so the unit suite stays runnable anywhere.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

for _rel in (
    "components/registry/src",
    "components/def-store/src",
    "components/template-store/src",
    "components/document-store/src",
    "libs/wip-auth/src",
):
    _p = str(_REPO_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

TEST_API_KEY = "test_api_key"
TEST_DB = "wip_toolkit_integration_test"

# Env must be in place before the service apps are imported (they read it
# at import time — document-store builds its auth middleware then).
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/")
os.environ["DATABASE_NAME"] = TEST_DB
os.environ["API_KEY"] = TEST_API_KEY
os.environ["MASTER_API_KEY"] = TEST_API_KEY
os.environ["AUTH_ENABLED"] = "true"
os.environ["REGISTRY_URL"] = "http://registry"
os.environ["REGISTRY_API_KEY"] = TEST_API_KEY
os.environ["DEF_STORE_URL"] = "http://def-store"
os.environ["DEF_STORE_API_KEY"] = TEST_API_KEY
os.environ["TEMPLATE_STORE_URL"] = "http://template-store"
os.environ["TEMPLATE_STORE_API_KEY"] = TEST_API_KEY
# File METADATA listing is pure Mongo — the exporter always lists files
# (even to record zero), and a disabled feature gate 503s the whole export.
# Blob I/O (MinIO) is never exercised here: the seed uploads no binaries.
os.environ["WIP_FILE_STORAGE_ENABLED"] = "true"
os.environ["WIP_AUTH_LEGACY_API_KEY"] = ""
# Unscoped admin key — the same semantics as a deployed master key.
# Namespace-scoped keys change value-resolution behavior (the key's scope
# is forwarded to the Registry, so a ["wip"]-scoped key resolves bare
# values in the WRONG namespace); the toolkit always passes namespace
# explicitly, so no test here relies on implicit derivation.
os.environ["WIP_AUTH_API_KEYS_JSON"] = json.dumps([{
    "name": "test",
    "key": TEST_API_KEY,
    "owner": "test",
    "groups": ["wip-admins"],
    "namespaces": None,
}])

SERVICE_HOSTS = ("registry", "def-store", "template-store", "document-store")


def _mongo_reachable() -> bool:
    try:
        with socket.create_connection(("localhost", 27017), timeout=1):
            return True
    except OSError:
        return False


class _HostRouter(httpx.AsyncBaseTransport):
    """Routes requests to in-process ASGI apps by URL hostname."""

    def __init__(self, transports: dict[str, httpx.ASGITransport]) -> None:
        self._transports = transports

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = self._transports.get(request.url.host)
        if transport is None:
            raise RuntimeError(
                f"integration harness: unexpected outbound request to "
                f"{request.url} — every host must map to an in-process app"
            )
        return await transport.handle_async_request(request)


class _SyncBridge(httpx.BaseTransport):
    """Sync transport for WIPClient: carries requests onto the portal loop.

    The response body is read inside the async context and re-wrapped as a
    plain sync Response — the ASGI response's stream is async and unusable
    from the sync client.
    """

    def __init__(self, portal, router: _HostRouter) -> None:
        self._portal = portal
        self._router = router

    async def _send(self, request: httpx.Request) -> httpx.Response:
        response = await self._router.handle_async_request(request)
        await response.aread()
        return response

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._portal.call(self._send, request)
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )


async def _bootstrap_fresh_instance() -> None:
    """What a freshly-started empty WIP instance contains before any user
    data: the default 'wip' namespace (registry startup) and def-store's
    system terminologies (its startup bootstrap — relation-type validation
    reads _ONTOLOGY_RELATIONSHIP_TYPES). Lifespans don't run under ASGI
    mounting, so the harness replays both, exactly like def-store's own
    conftest (hardcoded SYS-* IDs, no Registry registration — system data
    is never resolved via synonyms)."""
    from def_store.models.term import Term
    from def_store.models.terminology import Terminology
    from def_store.services.system_terminologies import SYSTEM_TERMINOLOGIES
    from registry.models.namespace import Namespace

    await Namespace(prefix="wip", description="Test namespace").insert()
    for sys_term in SYSTEM_TERMINOLOGIES:
        terminology = Terminology(
            terminology_id=f"SYS-{sys_term['value']}",
            namespace="wip",
            value=sys_term["value"],
            label=sys_term["label"],
            description=sys_term.get("description", ""),
            case_sensitive=sys_term.get("case_sensitive", False),
            metadata=sys_term.get("metadata", {}),
            status="active",
            term_count=len(sys_term.get("terms", [])),
        )
        await terminology.insert()
        for j, t in enumerate(sys_term.get("terms", [])):
            await Term(
                term_id=f"SYS-T-{sys_term['value']}-{j}",
                namespace="wip",
                terminology_id=terminology.terminology_id,
                value=t["value"],
                label=t.get("label", t["value"]),
                description=t.get("description", ""),
                status="active",
                sort_order=t.get("sort_order", j),
                metadata=t.get("metadata", {}),
            ).insert()


def _clear_shared_caches() -> None:
    """Restore mode recreates the SAME entity IDs, so any warm cache would
    answer lookups the services should be answering and mask a regression."""
    from def_store.services.ontology_service import OntologyService
    from wip_auth.resolve import clear_resolution_cache

    clear_resolution_cache()
    OntologyService.invalidate_relation_type_cache()


class IntegrationStack:
    """Handle the tests use: portal, router, wipe(), and model registry."""

    def __init__(self, portal, router: _HostRouter, models: list) -> None:
        self.portal = portal
        self.router = router
        self.models = models

    def wipe(self) -> None:
        """Catastrophic-loss simulation: empty every collection, then
        re-bootstrap what a fresh instance's startup would recreate."""

        async def _wipe() -> None:
            for model in self.models:
                await model.delete_all()
            await _bootstrap_fresh_instance()

        self.portal.call(_wipe)
        _clear_shared_caches()


@pytest.fixture(scope="session")
def stack():
    if not _mongo_reachable():
        pytest.skip(
            "MongoDB not reachable on localhost:27017 — run via "
            "scripts/wip-test.sh wip-toolkit (auto-provisions test-mongo) "
            "or start one: podman run -d --name test-mongo -p 27017:27017 mongo:7"
        )

    try:
        from def_store.api.auth import set_api_key as def_store_set_api_key
        from def_store.main import app as def_store_app
        from def_store.models.audit_log import TermAuditLog
        from def_store.models.term import Term
        from def_store.models.term_relation import TermRelation
        from def_store.models.terminology import Terminology
        from document_store.main import app as document_store_app
        from document_store.models.backup_job import BackupJob
        from document_store.models.document import Document
        from document_store.models.file import File
        from registry.main import app as registry_app
        from registry.models.composite_key_claim import CompositeKeyClaim
        from registry.models.deletion_journal import DeletionJournal
        from registry.models.entry import RegistryEntry
        from registry.models.grant import NamespaceGrant
        from registry.models.id_counter import IdCounter
        from registry.models.namespace import Namespace
        from registry.services.auth import AuthService
        from template_store.api.auth import set_api_key as template_store_set_api_key
        from template_store.main import app as template_store_app
        from template_store.models.template import Template
    except ImportError as e:
        pytest.skip(
            f"service packages not importable ({e}) — install the four "
            f"services' requirements into the venv (the CI toolkit job and "
            f"the standard dev venv both have them)"
        )

    from anyio.from_thread import start_blocking_portal
    from beanie import init_beanie
    from motor.motor_asyncio import AsyncIOMotorClient

    models = [
        # Registry
        Namespace, RegistryEntry, IdCounter, NamespaceGrant,
        DeletionJournal, CompositeKeyClaim,
        # Def-Store
        Terminology, Term, TermRelation, TermAuditLog,
        # Template-Store
        Template,
        # Document-Store
        Document, BackupJob, File,
    ]

    apps = {
        "registry": registry_app,
        "def-store": def_store_app,
        "template-store": template_store_app,
        "document-store": document_store_app,
    }

    with start_blocking_portal() as portal:
        router = _HostRouter({
            host: httpx.ASGITransport(app=app) for host, app in apps.items()
        })

        async def _init() -> AsyncIOMotorClient:
            mongo = AsyncIOMotorClient(
                os.environ["MONGO_URI"], serverSelectionTimeoutMS=5000
            )
            await mongo.admin.command("ping")
            # One init_beanie over all models — repeated calls rebind
            # models to the wrong database (the components' conftests
            # learned this in CI).
            await init_beanie(database=mongo[TEST_DB], document_models=models)
            for model in models:
                await model.delete_all()
            await _bootstrap_fresh_instance()
            return mongo

        mongo_client = portal.call(_init)
        _clear_shared_caches()

        for app in apps.values():
            app.state.mongodb_client = mongo_client
        AuthService.initialize(master_key=TEST_API_KEY)
        def_store_set_api_key(TEST_API_KEY)
        template_store_set_api_key(TEST_API_KEY)

        # One patch wires every internal cross-service httpx.AsyncClient
        # to the in-process apps (services construct clients per call with
        # env base URLs — no seam needed, no service code touched).
        original_async_client = httpx.AsyncClient

        class _RoutedAsyncClient(original_async_client):  # type: ignore[valid-type,misc]
            def __init__(self, *args, **kwargs):
                if kwargs.get("transport") is None:
                    kwargs["transport"] = router
                super().__init__(*args, **kwargs)

        httpx.AsyncClient = _RoutedAsyncClient
        try:
            yield IntegrationStack(portal, router, models)
        finally:
            httpx.AsyncClient = original_async_client


@pytest.fixture()
def wip_client(stack):
    """A real WIPClient whose requests bridge into the in-process apps."""
    from wip_toolkit.client import WIPClient
    from wip_toolkit.config import WIPConfig

    config = WIPConfig(
        api_key=TEST_API_KEY,
        service_urls={host: f"http://{host}" for host in SERVICE_HOSTS},
    )
    client = WIPClient(config, transport=_SyncBridge(stack.portal, stack.router))
    yield client
    client.close()
