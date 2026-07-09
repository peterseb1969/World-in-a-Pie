"""Namespace-deletion journal tells the truth about MinIO/Postgres, and the
dry-run counts everything the deletion removes (CASE-646).

Two findings:

1. The module claimed "crash-safe across MongoDB, MinIO, and PostgreSQL",
   but MinIO/Postgres step failures were caught and returned 0-deleted
   *successes* — a journal could reach "completed" while data survived in
   those stores. Now a degraded best-effort step records `step.error` and
   is marked `completed_with_errors`, rolling up to a terminal journal
   status of `completed_with_warnings`. Deletion still never blocks on the
   secondary stores (Mongo is the system of record).

2. `dry_run`'s `_count_entities` hand-enumerated collections and omitted
   `composite_key_claims`, which the real deletion *does* remove. It now
   iterates the same `_REGISTRY_COLLECTIONS` list the journal builder uses,
   so the two cannot drift.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from registry.models.deletion_journal import DeletionJournal, DeletionStep
from registry.services.namespace_deletion import (
    _REGISTRY_COLLECTIONS,
    NamespaceDeletionService,
)


# ---------------------------------------------------------------------------
# Finding 1 — degraded best-effort steps are visible in the journal status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postgres_step_failure_flags_journal(client, monkeypatch):
    """A PostgreSQL cleanup that can't reach reporting-sync degrades the step
    and rolls the journal up to completed_with_warnings — not a bare
    completed — without raising. (client fixture initializes Beanie so the
    journal can persist.)"""
    import httpx as _httpx

    svc = NamespaceDeletionService()
    svc._postgres_url = "http://unreachable-reporting-sync:8005"

    journal = DeletionJournal(
        namespace="ns-x-646",
        steps=[DeletionStep(order=1, store="postgresql", detail="postgres rows")],
    )
    await journal.insert()

    with patch("registry.services.namespace_deletion.httpx.AsyncClient") as mock_client:
        instance = mock_client.return_value
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.delete = AsyncMock(side_effect=_httpx.ConnectError("unreachable"))
        await svc._execute_journal(journal)

    step = journal.steps[0]
    assert step.status == "completed_with_errors"
    assert step.error and "rows may remain" in step.error
    assert journal.status == "completed_with_warnings"


@pytest.mark.asyncio
async def test_clean_journal_stays_completed(client, monkeypatch):
    """A journal whose steps all succeed cleanly still terminates as plain
    'completed' — the new status only appears on real degradation."""
    svc = NamespaceDeletionService()
    journal = DeletionJournal(
        namespace="ns-clean-646",
        steps=[DeletionStep(order=1, store="mongodb", collection="registry_entries",
                            detail="entries")],
    )
    await journal.insert()
    monkeypatch.setattr(svc, "_exec_mongodb_step", AsyncMock(return_value=3))

    await svc._execute_journal(journal)

    assert journal.steps[0].status == "completed"
    assert journal.status == "completed"


# ---------------------------------------------------------------------------
# Finding 2 — dry-run counts composite_key_claims (and can't drift)
# ---------------------------------------------------------------------------


def test_composite_key_claims_in_registry_collections():
    """Guard the precondition: the collection the dry-run must count is in the
    list both the counter and the journal builder iterate."""
    names = {name for name, _ in _REGISTRY_COLLECTIONS}
    assert "composite_key_claims" in names


@pytest.mark.asyncio
async def test_dry_run_counts_composite_key_claims(
    client: AsyncClient, auth_headers: dict
):
    """A namespace with registered entries (each mints a composite_key_claim)
    reports a non-zero composite_key_claims count in the dry-run — it was
    silently omitted before."""
    await client.post(
        "/api/registry/namespaces",
        json={"prefix": "dr-claims-646", "deletion_mode": "full"},
        headers=auth_headers,
    )
    await client.post(
        "/api/registry/entries/register",
        json=[
            {"namespace": "dr-claims-646", "entity_type": "templates",
             "composite_key": {"value": "claim_a"}, "created_by": "test"},
            {"namespace": "dr-claims-646", "entity_type": "templates",
             "composite_key": {"value": "claim_b"}, "created_by": "test"},
        ],
        headers=auth_headers,
    )

    resp = await client.delete(
        "/api/registry/namespaces/dr-claims-646",
        params={"dry_run": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    counts = resp.json()["entity_counts"]
    assert "composite_key_claims" in counts
    assert counts["composite_key_claims"] >= 2
    # The pre-existing keys still count, proving no regression.
    assert counts["registry_entries"] == 2
