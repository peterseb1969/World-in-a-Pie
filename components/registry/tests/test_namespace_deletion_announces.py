"""Every namespace-deletion run tells other services' caches the namespace
is gone, whether the run finished, failed or is waiting on a service; and an
API deletion reaches the announcement too."""

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

import registry.services.namespace_deletion as mod
from registry.models.deletion_journal import DeletionJournal, DeletionStep
from registry.services.namespace_deletion import NamespaceDeletionService


@pytest.fixture
def announced(monkeypatch):
    announce = AsyncMock(return_value=True)
    monkeypatch.setattr(mod, "announce_namespace_deleted", announce)
    return announce


def _prefixes(announce: AsyncMock) -> list[str]:
    return [call.args[1] for call in announce.await_args_list]


async def _journal(prefix: str) -> DeletionJournal:
    journal = DeletionJournal(
        namespace=prefix,
        steps=[DeletionStep(order=1, store="mongodb", collection="registry_entries",
                            filter={"namespace": prefix}, detail="entries")],
    )
    await journal.insert()
    return journal


async def test_a_finished_run_announces(client, announced):
    journal = await _journal("ann-done")

    await NamespaceDeletionService()._execute_journal(journal)

    assert journal.status == "completed"
    assert _prefixes(announced) == ["ann-done"]


async def test_a_failed_run_announces_too(client, announced, monkeypatch):
    svc = NamespaceDeletionService()
    monkeypatch.setattr(svc, "_execute_step", AsyncMock(side_effect=RuntimeError("boom")))
    journal = await _journal("ann-failed")

    with pytest.raises(RuntimeError):
        await svc._execute_journal(journal)

    assert journal.status == "failed"
    assert _prefixes(announced) == ["ann-failed"]


async def test_an_api_deletion_announces(client: AsyncClient, auth_headers: dict, announced):
    await client.post(
        "/api/registry/namespaces",
        json={"prefix": "ann-api", "deletion_mode": "full"},
        headers=auth_headers,
    )

    resp = await client.delete("/api/registry/namespaces/ann-api", headers=auth_headers)

    assert resp.status_code == 200 and resp.json()["status"] == "completed"
    assert _prefixes(announced) == ["ann-api"]


async def test_without_nats_a_deletion_still_completes(client: AsyncClient, auth_headers: dict):
    # The test app starts no bus: the announcement is a no-op and the TTL is
    # the whole mechanism.
    assert not mod.get_event_bus().configured
    await client.post(
        "/api/registry/namespaces",
        json={"prefix": "ann-nonats", "deletion_mode": "full"},
        headers=auth_headers,
    )

    resp = await client.delete("/api/registry/namespaces/ann-nonats", headers=auth_headers)

    assert resp.status_code == 200 and resp.json()["status"] == "completed"
