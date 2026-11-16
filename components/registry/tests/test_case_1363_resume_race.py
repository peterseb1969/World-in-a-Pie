"""A restore that lands between a resume's up-front check and its claim is
kept by every step.

The up-front check (_refuse_resume_onto_live_namespace) reads the prefix
once, before the resume claims the journal. An ID-preserving restore that
inserts the namespace after that read gives it the deleted namespace's id, so
a step check comparing ids alone took it for the deletion's own namespace and
deleted its data. While steps run, the only record that may hold the prefix is
the deletion's lock: any other is a namespace created again.
"""

import uuid

import pytest

from registry.models.namespace import Namespace
from registry.services import namespace_deletion
from tests.test_case_1283_registry_review import (
    NS,
    _entries,
    _journal,
    _restored_under,
)


@pytest.mark.asyncio
async def test_a_restore_landing_after_the_resume_check_keeps_its_data(
    client, auth_headers, monkeypatch,
):
    prefix = f"race-{uuid.uuid4().hex[:8]}"
    await _journal(prefix, "completed_with_warnings")
    original = namespace_deletion._refuse_resume_onto_live_namespace

    async def check_then_restore_lands(journal):
        await original(journal)  # finds no live namespace: passes
        await _restored_under(prefix, "old-id")  # the restore inserts now

    monkeypatch.setattr(
        namespace_deletion, "_refuse_resume_onto_live_namespace", check_then_restore_lands,
    )

    r = await client.post(f"{NS}/{prefix}/resume-delete", headers=auth_headers)

    assert r.status_code == 200, r.text
    assert await _entries(prefix) == 1
    assert (await Namespace.find_one({"prefix": prefix})).status == "active"


@pytest.mark.asyncio
async def test_the_deletions_own_lock_is_not_a_namespace_created_again(client):
    """The deletion's own locked record, same id, is the one being deleted."""
    prefix = f"race-{uuid.uuid4().hex[:8]}"
    await Namespace(prefix=prefix, namespace_id="old-id", status="locked").insert()
    journal = type("J", (), {"namespace": prefix, "namespace_id": "old-id"})()
    service = namespace_deletion.NamespaceDeletionService()
    assert await service._namespace_recreated(journal) is False
