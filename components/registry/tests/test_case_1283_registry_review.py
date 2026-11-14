"""A namespace name is not created again while its deletion has steps left.

Provenance: CASE-1283. A deletion journal's later steps tell the namespace
they delete from a namespace created again under its name by namespace id,
and keep the new one's data. Creating the name while the old deletion was in
progress or stopped on a failed step therefore left the old namespace's
stored files and reporting rows behind for good. The create (POST, and the
upsert's create, dry runs included) now answers 409 until the deletion is
resumed to its end; a deletion that completed with warnings ran every step
and does not hold the name.

Also: a reservation holder's upsert of a locked namespace answered 200 (it
would write nothing), while every other caller got 409; the refusal of an
archived id onto a name reserved for another id did not say how to avoid
it; and an ambiguous answer cut short at the candidate limit stated the
fetch size as the number of matches.
"""

import uuid

import pytest

from registry.models.deletion_journal import DeletionJournal, DeletionStep
from registry.models.namespace import Namespace
from registry.models.reservation import RuntimeReservation
from tests.test_case_1096_upsert_dry_run import app_key  # noqa: F401

NS = "/api/registry/namespaces"


async def _journal(prefix: str, status: str) -> None:
    await DeletionJournal(namespace=prefix, namespace_id="old-id", status=status, steps=[
        DeletionStep(order=1, store="mongodb", collection="registry_entries",
                     filter={"namespace": prefix}),
    ]).insert()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["in_progress", "failed"])
async def test_a_create_is_refused_while_the_deletion_has_steps_left(client, auth_headers, status):
    prefix = f"gate-{uuid.uuid4().hex[:8]}"
    await _journal(prefix, status)
    for params in ({"dry_run": "true"}, {}):
        r = await client.put(f"{NS}/{prefix}", json={}, params=params, headers=auth_headers)
        assert r.status_code == 409, (params, r.text)
        assert "resume-delete" in r.json()["detail"]
    r = await client.post(NS, json={"prefix": prefix}, headers=auth_headers)
    assert r.status_code == 409, r.text
    assert "resume-delete" in r.json()["detail"]
    assert await Namespace.find_one({"prefix": prefix}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "completed_with_warnings"])
async def test_a_deletion_that_ran_every_step_does_not_hold_the_name(client, auth_headers, status):
    prefix = f"gate-{uuid.uuid4().hex[:8]}"
    await _journal(prefix, status)
    r = await client.put(f"{NS}/{prefix}", json={}, headers=auth_headers)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_reservation_holder_gets_409_for_a_locked_namespace(
    client, auth_headers, app_key  # noqa: F811
):
    """The key owns the runtime reservation, so it passes the upsert without
    a grant on the namespace (which a lock withholds)."""
    headers, prefix = app_key
    await RuntimeReservation(prefix=prefix, app=f"app-{prefix}").insert()
    created = await client.put(f"{NS}/{prefix}", json={}, headers=auth_headers)
    assert created.status_code == 200, created.text
    await Namespace.get_motor_collection().update_one({"prefix": prefix}, {"$set": {"status": "locked"}})
    r = await client.put(f"{NS}/{prefix}", json={}, headers=headers)
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_an_archived_id_onto_a_key_reservation_says_how_to_proceed(client, auth_headers):
    """The refusal of an ID-preserving restore onto a name reserved for
    another id states the order that avoids it and the way out."""
    from tests.test_case_1084_namespace_ids import _key, _p, _upsert, _uuid

    prefix = _p("taken")
    await _key(client, auth_headers, _p("owner"), [prefix], reserve=[{"prefix": prefix}])
    resp = await _upsert(client, auth_headers, prefix, namespace_id=_uuid())
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "Restore a namespace's data before deploying the apps" in detail
    assert f"DELETE /api/registry/reservations/{prefix}" in detail


def test_an_archived_id_onto_an_install_reservation_names_wip_deploy():
    from types import SimpleNamespace

    from registry.services.namespace_identity import _reserved_conflict

    reservation = SimpleNamespace(
        prefix="kb", app="wip-kb", managed_by="wip-deploy", namespace_id="reserved-id",
    )
    detail = _reserved_conflict("kb", reservation, "archived-id")
    assert "Restore a namespace's data before deploying the apps" in detail
    assert "remove the app with wip-deploy" in detail
    assert "reserved-id" in detail and "archived-id" in detail


def test_an_ambiguous_answer_cut_short_does_not_state_an_exact_count():
    """The matches are fetched one past the candidate limit, so a full fetch
    says only that there are more; it was reported as that many."""
    from registry.api.entries import _RESOLVE_CANDIDATE_LIMIT, _match_count

    full = [object()] * (_RESOLVE_CANDIDATE_LIMIT + 1)
    assert _match_count(full, full) == (f"more than {_RESOLVE_CANDIDATE_LIMIT}", True)
    assert _match_count(full, full[:4]) == ("4 or more", True)
    assert _match_count(full[:3], full[:3]) == ("3", False)


# ── A resume never deletes a namespace that holds the name again ────────


async def _restored_under(prefix: str, namespace_id: str) -> None:
    """The name created again, as an ID-preserving restore does: the new
    namespace takes ``namespace_id``, with an entry of its own."""
    await Namespace(prefix=prefix, namespace_id=namespace_id, description="restored").insert()
    await _entry_in(prefix)


async def _entry_in(prefix: str) -> None:
    from registry.models.entry import RegistryEntry

    await RegistryEntry(
        entry_id=f"kept-{prefix}", namespace=prefix, entity_type="terms",
        primary_composite_key={"value": "kept"}, primary_composite_key_hash=f"h-{prefix}",
        status="active", created_by="test",
    ).insert()


async def _entries(prefix: str) -> int:
    from registry.models.entry import RegistryEntry

    return await RegistryEntry.find({"namespace": prefix}).count()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed_with_warnings", "failed", "in_progress"])
async def test_a_resume_onto_a_namespace_restored_under_its_id_is_refused(client, auth_headers, status):
    """A deletion that completed with warnings does not hold the name, so an
    ID-preserving restore re-creates the namespace under the same id. A
    resume then ran the steps by namespace against the restored data, and
    the id comparison could not tell. It is refused, and nothing is deleted."""
    prefix = f"resume-{uuid.uuid4().hex[:8]}"
    await _journal(prefix, status)
    await _restored_under(prefix, "old-id")

    r = await client.post(f"{NS}/{prefix}/resume-delete", headers=auth_headers)

    assert r.status_code == 409, r.text
    assert "exists again" in r.json()["detail"]
    assert await _entries(prefix) == 1
    assert (await Namespace.find_one({"prefix": prefix})).status == "active"


@pytest.mark.asyncio
async def test_a_resume_with_another_namespace_under_the_name_keeps_its_data(client, auth_headers):
    """A namespace under another id is told apart by its id: the resume goes
    ahead and keeps the new namespace's data, as before."""
    prefix = f"resume-{uuid.uuid4().hex[:8]}"
    await _journal(prefix, "completed_with_warnings")
    await _restored_under(prefix, "new-id")

    r = await client.post(f"{NS}/{prefix}/resume-delete", headers=auth_headers)

    assert r.status_code == 200, r.text
    assert await _entries(prefix) == 1
