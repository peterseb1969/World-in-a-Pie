"""CASE-1267 — the deletion journal's object-store total covers every batch.

Stored objects are one journal step per _STORAGE_DELETE_BATCH keys: a journal
is a single MongoDB document, and every key inline on one step would outgrow
its 16 MiB limit. All those steps report under the summary's one
``minio_objects`` key, so the key has to add the batches up — otherwise it
holds the last batch only, and a namespace whose dry run promised 2500
objects closes with a journal saying 500.
"""

import uuid

import pytest

from registry.models.deletion_journal import DeletionJournal
from registry.services.namespace_deletion import _STORAGE_DELETE_BATCH, NamespaceDeletionService


async def _journal_with_objects(client, monkeypatch, total: int):
    """A journal built the way a deletion builds it, for a namespace holding
    `total` stored objects, and a stubbed executor returning what document-store
    deletes for each step (test_case_876 pins the real step against
    document-store)."""
    prefix = f"del1267-{uuid.uuid4().hex[:6]}"
    svc = NamespaceDeletionService()
    svc._postgres_url = None

    async def keys(_prefix):
        return [f"k{i}" for i in range(total)]

    async def execute(self, step, namespace):
        return len(step.storage_keys or [])

    monkeypatch.setattr(svc, "_get_storage_keys", keys)
    monkeypatch.setattr(NamespaceDeletionService, "_execute_step", execute)
    journal = await svc._build_journal(prefix, False, None, [])
    await journal.insert()
    return svc, journal


@pytest.mark.asyncio
async def test_every_object_batch_counts_into_the_summary(client, monkeypatch):
    total = 2 * _STORAGE_DELETE_BATCH + 500
    svc, journal = await _journal_with_objects(client, monkeypatch, total)

    batches = [s for s in journal.steps if s.store == "file_storage"]
    assert [len(s.storage_keys or []) for s in batches] == [_STORAGE_DELETE_BATCH,
                                                             _STORAGE_DELETE_BATCH, 500]

    await svc._run_steps(journal)

    stored = await DeletionJournal.get(journal.id)
    assert stored.status == "completed"
    assert stored.summary["minio_objects"] == total


@pytest.mark.asyncio
async def test_a_resumed_journal_counts_each_batch_once(client, monkeypatch):
    """Resuming must not add the finished batches a second time: the summary
    is rebuilt from every step on each run."""
    total = 2 * _STORAGE_DELETE_BATCH + 500
    svc, journal = await _journal_with_objects(client, monkeypatch, total)

    batches = [s for s in journal.steps if s.store == "file_storage"]
    # The first two ran in an earlier life of this journal; the last is pending.
    for done in batches[:-1]:
        done.status = "completed"
        done.deleted_count = len(done.storage_keys or [])
    await journal.save()

    await svc._run_steps(journal)

    stored = await DeletionJournal.get(journal.id)
    assert stored.summary["minio_objects"] == total
