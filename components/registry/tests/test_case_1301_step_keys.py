"""CASE-1301 — a deletion's stored-object keys are bound to the step that recorded them.

document-store removes a namespace's stored objects on the Registry's behalf. It
authorised any key list that arrived while a deletion ran under the namespace's
name, because nothing in the request said which journal and step it came from.
The Registry now names its journal on deletion-status, answers the key set of
one file-storage step on a privileged route of its own, and puts the journal id
and step order in the request it sends — so what document-store deletes is what
this deletion recorded, and nothing else.
"""

import uuid

import pytest
from bson import ObjectId
from httpx import AsyncClient

import registry.services.namespace_deletion as mod
from registry.models.deletion_journal import DeletionJournal, DeletionStep

BASE = "/api/registry/namespaces"


def _prefix() -> str:
    return f"sk-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def app_key():
    """A config-file key with admin on one namespace and no groups (an app's
    key): it may not read what a deletion's steps name."""
    from registry.api import api_keys as api_keys_module
    from wip_auth.models import APIKeyRecord
    from wip_auth.providers.api_key import hash_api_key

    prefix = _prefix()
    plaintext = f"app-key-{uuid.uuid4().hex}"
    provider = api_keys_module._get_provider()
    before = [k for k in provider.iter_keys() if k.name in provider.config_key_names()]
    record = APIKeyRecord(
        name=f"app-{prefix}", key_hash=hash_api_key(plaintext), owner="system:test",
        namespaces=[prefix], grants={prefix: "admin"},
    )
    provider.reload_config_keys([*before, record])
    try:
        yield {"X-API-Key": plaintext}
    finally:
        provider.reload_config_keys(before)


async def _journal(prefix: str, *steps: DeletionStep, status: str = "in_progress"):
    journal = DeletionJournal(namespace=prefix, status=status, steps=list(steps))
    await journal.insert()
    return journal


async def _batched_step(prefix: str, keys: list[str], order: int = 1) -> DeletionStep:
    """A step of the shape current journals write: keys in their own document."""
    return DeletionStep(
        order=order, store="file_storage", action="delete_objects",
        storage_keys_batch=await mod._store_key_batch(prefix, keys),
    )


@pytest.mark.asyncio
async def test_deletion_status_names_the_journal(
    client: AsyncClient, auth_headers: dict,
):
    prefix = _prefix()
    journal = await _journal(prefix, await _batched_step(prefix, ["k1"]))

    body = (await client.get(f"{BASE}/{prefix}/deletion-status", headers=auth_headers)).json()

    assert body["journal_id"] == str(journal.id)


@pytest.mark.asyncio
async def test_the_step_route_answers_the_keys_its_step_recorded(
    client: AsyncClient, auth_headers: dict,
):
    prefix = _prefix()
    journal = await _journal(prefix, await _batched_step(prefix, ["k1", "k2"]))

    resp = await client.get(
        f"{BASE}/{prefix}/deletion-steps/1/keys", headers=auth_headers
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "namespace": prefix,
        "journal_id": str(journal.id),
        "step_order": 1,
        "keys_available": True,
        "reason": None,
        "keys": ["k1", "k2"],
    }


@pytest.mark.asyncio
async def test_an_older_journal_answers_from_its_inline_keys(
    client: AsyncClient, auth_headers: dict,
):
    """Journals written before the separate key documents hold their keys on
    the step; those steps stay verifiable."""
    prefix = _prefix()
    await _journal(prefix, DeletionStep(
        order=1, store="file_storage", action="delete_objects", storage_keys=["k1"],
    ))

    resp = await client.get(
        f"{BASE}/{prefix}/deletion-steps/1/keys", headers=auth_headers
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["keys"] == ["k1"]
    assert resp.json()["keys_available"] is True


@pytest.mark.asyncio
async def test_a_legacy_minio_step_is_the_same_step(
    client: AsyncClient, auth_headers: dict,
):
    prefix = _prefix()
    await _journal(prefix, DeletionStep(order=1, store="minio", storage_keys=["k1"]))

    resp = await client.get(
        f"{BASE}/{prefix}/deletion-steps/1/keys", headers=auth_headers
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["keys"] == ["k1"]


@pytest.mark.asyncio
async def test_a_step_whose_keys_are_gone_says_so(
    client: AsyncClient, auth_headers: dict,
):
    """A step whose key batch was dropped names no verifiable keys: the answer
    is unavailable with the reason, not an empty set that reads as "nothing to
    check"."""
    prefix = _prefix()
    journal = await _journal(prefix, DeletionStep(
        order=1, store="file_storage", storage_keys_batch=str(ObjectId()),
    ))

    resp = await client.get(
        f"{BASE}/{prefix}/deletion-steps/1/keys", headers=auth_headers
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["keys_available"] is False
    assert body["keys"] == []
    assert body["journal_id"] == str(journal.id)
    assert "missing" in body["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("step", [
    pytest.param(DeletionStep(order=1, store="mongodb", collection="entries",
                             filter={"namespace": "x"}), id="not_a_file_storage_step"),
    pytest.param(DeletionStep(order=2, store="file_storage", storage_keys=["k"]), id="other_order"),
])
async def test_a_step_that_names_no_file_storage_delete_answers_404(
    client: AsyncClient, auth_headers: dict, step: DeletionStep,
):
    prefix = _prefix()
    await _journal(prefix, step)

    resp = await client.get(
        f"{BASE}/{prefix}/deletion-steps/1/keys", headers=auth_headers
    )

    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_no_journal_and_no_step_answer_alike(client: AsyncClient, auth_headers: dict):
    """A namespace with no journal and a journal without that step are the same
    answer as elsewhere on these routes: nothing to delete on either."""
    prefix = _prefix()
    gone = (await client.get(f"{BASE}/{prefix}/deletion-steps/1/keys", headers=auth_headers))
    assert gone.status_code == 404

    await _journal(prefix, await _batched_step(prefix, ["k1"]))
    other_order = await client.get(
        f"{BASE}/{prefix}/deletion-steps/9/keys", headers=auth_headers
    )
    assert other_order.status_code == 404
    assert other_order.json()["detail"] == (
        f"Namespace '{prefix}' has no file-storage deletion step 9"
    )


@pytest.mark.asyncio
async def test_the_key_set_is_read_by_platform_admins_only(
    client: AsyncClient, auth_headers: dict, app_key,
):
    prefix = _prefix()
    await _journal(prefix, await _batched_step(prefix, ["k1"]))
    assert (await client.get(
        f"{BASE}/{prefix}/deletion-steps/1/keys", headers=auth_headers
    )).status_code == 200

    r = await client.get(f"{BASE}/{prefix}/deletion-steps/1/keys", headers=app_key)

    assert r.status_code == 404
    # The same answer as a namespace that never had a journal.
    assert r.json()["detail"] == f"No deletion journal found for namespace '{prefix}'"


@pytest.mark.asyncio
async def test_the_request_names_the_journal_and_step_it_comes_from(monkeypatch):
    """The Registry sends the binding document-store checks."""
    rec_posts: list[dict] = []

    class _Response:
        status_code = 200
        text = ""

        def json(self):
            return {"deleted": 1, "refused": [], "failed": []}

    class _Client:
        def __call__(self, *a, **kw):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            rec_posts.append(json)
            return _Response()

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client())
    svc = mod.NamespaceDeletionService()
    svc._document_store_url = "http://document-store.test"
    step = DeletionStep(order=3, store="file_storage", storage_keys=["k1"])

    assert await svc._exec_file_storage_step(step, "probe-ns", "JOURNAL-ID") == 1
    assert rec_posts[0] == {
        "namespace": "probe-ns",
        "journal_id": "JOURNAL-ID",
        "step_order": 3,
        "storage_keys": ["k1"],
    }
