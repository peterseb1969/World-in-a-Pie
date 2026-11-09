"""A namespace deletion's step runner, pinned before _run_steps' refactor.

Provenance: CASE-1161. The suite never reached these branches: on a resumed
journal, completed object-store and PostgreSQL steps count into the summary
under minio_objects / postgres_rows, and an executed object-store step does
too. Also pinned: an executed step's count, a degraded step rolling the
journal up to completed_with_warnings, and a failing step failing the
journal and propagating.
"""

import uuid

import pytest

from registry.models.deletion_journal import DeletionJournal, DeletionStep
from registry.services.namespace_deletion import NamespaceDeletionService


def _step(order: int, store: str, *, status: str = "pending", count: int = 0, collection=None) -> DeletionStep:
    return DeletionStep(
        order=order, store=store, collection=collection, detail=f"step {order}",
        status=status, deleted_count=count,
    )


async def _journal(*steps: DeletionStep) -> DeletionJournal:
    journal = DeletionJournal(namespace=f"rs1161-{uuid.uuid4().hex[:6]}", steps=list(steps), status="in_progress")
    await journal.insert()
    return journal


@pytest.mark.asyncio
async def test_a_resumed_journal_counts_completed_steps_of_every_store(client, monkeypatch):
    executed: list[int] = []

    async def execute(self, step, namespace):
        executed.append(step.order)
        if step.order == 5:
            step.error = "object store slow; 2 keys left"
        return {4: 3, 5: 7}[step.order]

    monkeypatch.setattr(NamespaceDeletionService, "_execute_step", execute)
    journal = await _journal(
        _step(1, "mongodb", status="completed", count=10, collection="entries"),
        _step(2, "file_storage", status="completed", count=4),
        _step(3, "postgresql", status="completed", count=6),
        _step(4, "mongodb", collection="grants"),
        _step(5, "minio"),
    )
    await NamespaceDeletionService()._run_steps(journal)
    assert executed == [4, 5]
    stored = await DeletionJournal.get(journal.id)
    assert stored.summary == {"entries": 10, "minio_objects": 7, "postgres_rows": 6, "grants": 3}
    assert [s.status for s in stored.steps] == [
        "completed", "completed", "completed", "completed", "completed_with_errors",
    ]
    assert stored.status == "completed_with_warnings" and stored.completed_at is not None


@pytest.mark.asyncio
async def test_a_failing_step_fails_the_journal(client, monkeypatch):
    async def execute(self, step, namespace):
        raise RuntimeError("mongo gone")

    monkeypatch.setattr(NamespaceDeletionService, "_execute_step", execute)
    journal = await _journal(_step(1, "mongodb", collection="entries"), _step(2, "mongodb", collection="grants"))
    with pytest.raises(RuntimeError, match="mongo gone"):
        await NamespaceDeletionService()._run_steps(journal)
    stored = await DeletionJournal.get(journal.id)
    assert stored.status == "failed"
    assert [(s.status, s.error) for s in stored.steps] == [("failed", "mongo gone"), ("pending", None)]
