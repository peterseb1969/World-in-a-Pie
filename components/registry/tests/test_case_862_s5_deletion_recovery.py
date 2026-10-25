"""Namespace-deletion cleanup that waits on an unreachable service is retried.

Provenance: CASE-862 S5 — a best-effort step whose service was not up yet
(reporting-sync, document-store) closed the journal as completed_with_warnings,
which recovery never selects again, so the data stayed behind for good; and
recovery ran inline at startup. Now such a step stays pending, the journal
stays in progress, and a background loop retries it until it runs.
"""

from unittest.mock import AsyncMock

import pytest

import registry.services.namespace_deletion as mod
from registry.models.deletion_journal import DeletionJournal, DeletionStep
from registry.models.namespace import Namespace
from registry.services.namespace_deletion import NamespaceDeletionService

# A closed port: a real connection refusal, no mocking.
UNREACHABLE = "http://127.0.0.1:9"


async def _journal(prefix: str) -> DeletionJournal:
    journal = DeletionJournal(
        namespace=prefix,
        steps=[
            DeletionStep(order=1, store="mongodb", collection="registry_entries",
                         filter={"namespace": prefix}, detail="entries"),
            DeletionStep(order=2, store="postgresql", detail="postgres rows"),
        ],
    )
    await journal.insert()
    return journal


@pytest.mark.asyncio
async def test_unreachable_reporting_sync_defers_the_step(client, monkeypatch):
    svc = NamespaceDeletionService()
    svc._postgres_url = UNREACHABLE
    monkeypatch.setattr(mod, "_service_api_key", lambda: "k")
    journal = await _journal("c862-s5-defer")

    await svc._execute_journal(journal)

    assert journal.status == "in_progress"
    assert journal.steps[0].status == "completed"  # the MongoDB step still ran
    assert journal.steps[1].status == "pending"
    assert "will retry" in journal.steps[1].error
    stored = await DeletionJournal.get(journal.id)
    assert stored.status == "in_progress"


@pytest.mark.asyncio
async def test_recovery_finishes_the_journal_once_the_service_answers(client, monkeypatch):
    svc = NamespaceDeletionService()
    svc._postgres_url = UNREACHABLE
    monkeypatch.setattr(mod, "_service_api_key", lambda: "k")
    journal = await _journal("c862-s5-recover")
    await svc._execute_journal(journal)
    assert journal.status == "in_progress"

    # reporting-sync is up now
    monkeypatch.setattr(svc, "_exec_postgresql_step", AsyncMock(return_value=7))
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(mod, "_recovery_sleep", fake_sleep)
    await svc.recover_until_done()

    stored = await DeletionJournal.get(journal.id)
    assert stored.status == "completed"
    assert stored.steps[1].status == "completed"
    assert stored.steps[1].deleted_count == 7


@pytest.mark.asyncio
async def test_a_recreated_namespace_keeps_its_reporting_data(client, monkeypatch):
    """A deferred cleanup must never delete the rows of a namespace that was
    created again under the same prefix after the deletion."""
    svc = NamespaceDeletionService()
    svc._postgres_url = UNREACHABLE
    monkeypatch.setattr(mod, "_service_api_key", lambda: "k")
    prefix = "c862-s5-reborn"
    journal = await _journal(prefix)
    await svc._execute_journal(journal)
    assert journal.steps[1].status == "pending"

    await Namespace(prefix=prefix, description="re-created").insert()
    cleanup = AsyncMock(return_value=99)
    monkeypatch.setattr(svc, "_exec_postgresql_step", cleanup)
    await svc.recover_incomplete_deletions()

    stored = await DeletionJournal.get(journal.id)
    cleanup.assert_not_awaited()  # the new namespace's rows were never touched
    assert stored.steps[1].status == "completed_with_errors"
    assert "exists again" in stored.steps[1].error
    assert stored.status == "completed_with_warnings"


@pytest.mark.asyncio
async def test_recovery_skips_a_journal_this_process_is_running(client, monkeypatch):
    svc = NamespaceDeletionService()
    journal = await _journal("c862-s5-busy")
    svc._running.add(str(journal.id))
    run = AsyncMock()
    monkeypatch.setattr(svc, "_run_steps", run)

    await svc.recover_incomplete_deletions()

    assert all(call.args[0].id != journal.id for call in run.await_args_list)


@pytest.mark.asyncio
async def test_a_deferral_long_after_startup_starts_the_recovery_loop(client, monkeypatch):
    """The startup loop ends once nothing is pending; a deletion deferred later
    must start it again, or its cleanup would wait for the next restart."""
    svc = NamespaceDeletionService()
    svc._postgres_url = UNREACHABLE
    monkeypatch.setattr(mod, "_service_api_key", lambda: "k")
    gate = []

    async def wait_for_service(seconds: float) -> None:
        # reporting-sync comes up while the loop sleeps
        monkeypatch.setattr(svc, "_exec_postgresql_step", AsyncMock(return_value=3))
        gate.append(seconds)

    monkeypatch.setattr(mod, "_recovery_sleep", wait_for_service)
    journal = await _journal("c862-s5-late")

    await svc._execute_journal(journal)  # a live deletion, no startup involved
    assert svc._recovery_task is not None
    await svc._recovery_task

    stored = await DeletionJournal.get(journal.id)
    assert stored.status == "completed"
