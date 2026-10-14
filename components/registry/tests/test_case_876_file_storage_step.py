"""CASE-876 — the namespace-deletion journal deletes stored objects via document-store.

The Registry's own object-store step was only built when MINIO_URL was set,
which no deployer ever did, and it authenticated with HTTP basic auth, which
MinIO and Garage both reject. Every namespace deletion therefore reported
"completed" while leaving its stored files behind. The step is now always
built when the namespace has files, and it sends the journal's persisted
storage keys to document-store, which owns the storage client.
"""

import httpx
import pytest

import registry.services.namespace_deletion as mod
from registry.models.deletion_journal import DeletionStep
from registry.services.namespace_deletion import NamespaceDeletionService

DOC_STORE = "http://document-store.test"


class _Recorder:
    """Stands in for httpx.AsyncClient; answers each POST from `respond`."""

    def __init__(self, respond):
        self.respond = respond
        self.posts: list[tuple[str, dict, dict]] = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        self.posts.append((url, json, headers))
        return self.respond(json)


class _Response:
    def __init__(self, status_code: int, body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


def _all_deleted(payload: dict) -> _Response:
    return _Response(200, {
        "namespace": payload["namespace"],
        "deleted": len(payload["storage_keys"]),
        "refused": [],
        "failed": [],
    })


@pytest.fixture
def service():
    svc = NamespaceDeletionService()
    svc._document_store_url = DOC_STORE
    return svc


def _step(keys: list[str], store: str = "file_storage") -> DeletionStep:
    return DeletionStep(order=1, store=store, action="delete_objects", storage_keys=keys)


async def test_journal_builds_the_step_without_any_storage_env(client, monkeypatch):
    # `client` initialises Beanie; _build_journal needs it for the Mongo steps.
    for var in ("MINIO_URL", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_BUCKET"):
        monkeypatch.delenv(var, raising=False)
    svc = NamespaceDeletionService()

    async def keys(prefix):
        return ["k1", "k2"]

    monkeypatch.setattr(svc, "_get_storage_keys", keys)
    journal = await svc._build_journal("probe-ns", False, None, [])

    first = journal.steps[0]
    assert first.store == "file_storage"
    assert first.storage_keys == ["k1", "k2"]
    # It is the first step, so it runs before any metadata (the files
    # records included) is deleted.
    assert all(s.order > first.order for s in journal.steps[1:])


async def test_keys_go_to_document_store_in_batches(service, monkeypatch):
    rec = _Recorder(_all_deleted)
    monkeypatch.setattr(mod.httpx, "AsyncClient", rec)
    keys = [f"k{i}" for i in range(2500)]
    step = _step(keys)

    deleted = await service._execute_step(step, "probe-ns")

    assert deleted == 2500
    assert step.error is None
    assert [len(p[1]["storage_keys"]) for p in rec.posts] == [1000, 1000, 500]
    url, payload, headers = rec.posts[0]
    assert url == f"{DOC_STORE}/api/document-store/files/storage/delete"
    assert payload["namespace"] == "probe-ns"
    assert headers == {"X-API-Key": mod._service_api_key()}


async def test_legacy_minio_step_runs_the_same_way(service, monkeypatch):
    rec = _Recorder(_all_deleted)
    monkeypatch.setattr(mod.httpx, "AsyncClient", rec)

    assert await service._execute_step(_step(["k1"], store="minio"), "probe-ns") == 1
    assert len(rec.posts) == 1


async def test_refused_and_failed_keys_degrade_the_step(service, monkeypatch):
    def respond(payload):
        return _Response(200, {
            "deleted": 1,
            "refused": [{"storage_key": "k2", "owner_namespaces": ["other"]}],
            "failed": [{"storage_key": "k3", "error": "boom"}],
        })

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Recorder(respond))
    step = _step(["k1", "k2", "k3"])

    assert await service._execute_step(step, "probe-ns") == 1
    assert step.error is not None
    assert "1 of 3" in step.error
    assert "refused" in step.error and "failed" in step.error


async def test_document_store_error_status_degrades_the_step(service, monkeypatch):
    monkeypatch.setattr(
        mod.httpx, "AsyncClient", _Recorder(lambda p: _Response(409, text="namespace is 'active'"))
    )
    step = _step(["k1"])

    assert await service._execute_step(step, "probe-ns") == 0
    assert "409" in step.error


async def test_unreachable_document_store_degrades_the_step(service, monkeypatch):
    def respond(payload):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Recorder(respond))
    step = _step(["k1"])

    assert await service._execute_step(step, "probe-ns") == 0
    assert "could not reach document-store" in step.error


async def test_missing_document_store_url_degrades_without_calling(service, monkeypatch):
    rec = _Recorder(_all_deleted)
    monkeypatch.setattr(mod.httpx, "AsyncClient", rec)
    service._document_store_url = None
    step = _step(["k1"])

    assert await service._execute_step(step, "probe-ns") == 0
    assert "DOCUMENT_STORE_URL" in step.error
    assert rec.posts == []


async def test_a_rerun_does_not_keep_the_previous_error(service, monkeypatch):
    monkeypatch.setattr(mod.httpx, "AsyncClient", _Recorder(_all_deleted))
    step = _step(["k1"])
    step.error = "could not reach document-store: earlier attempt"

    assert await service._execute_step(step, "probe-ns") == 1
    assert step.error is None
