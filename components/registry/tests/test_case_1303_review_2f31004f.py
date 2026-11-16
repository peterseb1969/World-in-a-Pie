"""What the review of the CASE-1303 follow-up (2f31004f) found.

That commit left an install's reservation of an absent namespace out of the
system backup altogether. A mirror restore then read the archive as "the source
holds no reservation of this prefix" and released the target's reservation of
that name, taking away a claim the target had made. The source did hold a
reservation: it reserved the name for its app, with no namespace of it. The
backup carries the record again, with no namespace id, and the restore still
skips it (`managed_on_source`), so nothing is written and no fresh id is pinned
for the namespace restore that follows.
"""

import uuid

import pytest

from registry.models.reservation import RuntimeReservation

from .test_case_1303_install_reservation_id import (
    BACKUP,
    KEYS,
    NS,
    RESTORE,
    _p,
    _reserved,
    _uuid,
    install_reserves,  # noqa: F401  (fixture)
)


async def _backup(client, headers) -> dict:
    resp = await client.post(BACKUP, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _mirror(client, headers, archive: dict, dry_run: bool = False) -> dict:
    resp = await client.post(
        RESTORE, json=archive, params={"dry_run": str(dry_run).lower(), "mode": "mirror"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _skipped(report: dict, kind: str) -> dict[str, str]:
    return {s["id"]: s["reason_code"] for s in report[kind]["skipped"]}


@pytest.mark.asyncio
async def test_the_backup_carries_an_install_reservation_that_holds_no_id(
    client, auth_headers, install_reserves,  # noqa: F811
):
    absent, present = _p("kb"), _p("lib")
    await install_reserves(absent, present)
    created = await client.put(f"{NS}/{present}", headers=auth_headers, json={})
    assert created.status_code == 200, created.text

    records = {r["prefix"]: r for r in (await _backup(client, auth_headers))["reservations"]}
    assert records[absent]["namespace_id"] is None
    assert records[absent]["managed_by"] == "wip-deploy"
    assert records[present]["namespace_id"] == created.json()["namespace_id"]


@pytest.mark.asyncio
async def test_a_mirror_keeps_a_reservation_of_a_name_the_source_only_reserved(
    client, auth_headers, install_reserves,  # noqa: F811
):
    """The target holds a runtime reservation of the name, the source an install
    one of no namespace: the archive names the prefix, so the mirror leaves the
    claim here alone."""
    prefix = _p("kb")
    await install_reserves(prefix)
    archive = await _backup(client, auth_headers)
    assert prefix in {r["prefix"] for r in archive["reservations"]}

    here_id = _uuid()
    await RuntimeReservation.get_motor_collection().update_one(
        {"prefix": prefix}, {"$set": {"managed_by": None, "namespace_id": here_id}},
    )

    report = await _mirror(client, auth_headers, archive)
    assert prefix not in report["reservations"]["deleted"]
    assert _skipped(report, "reservations")[prefix] == "managed_on_source"
    kept = await _reserved(prefix)
    assert kept is not None and kept.managed_by is None and kept.namespace_id == here_id


@pytest.mark.asyncio
async def test_a_dry_run_mirror_reports_that_same_reservation_as_kept(
    client, auth_headers, install_reserves,  # noqa: F811
):
    prefix = _p("kb")
    await install_reserves(prefix)
    archive = await _backup(client, auth_headers)
    await RuntimeReservation.get_motor_collection().update_one(
        {"prefix": prefix}, {"$set": {"managed_by": None, "namespace_id": _uuid()}},
    )

    report = await _mirror(client, auth_headers, archive, dry_run=True)
    assert prefix not in report["reservations"]["deleted"]
    assert await _reserved(prefix) is not None


@pytest.mark.asyncio
async def test_a_mirror_still_releases_a_reservation_the_archive_does_not_name(
    client, auth_headers,
):
    """Naming the id-less reservation bounds the mirror; it does not weaken it:
    a prefix the source holds no reservation of is still released here."""
    prefix, key = _p("late"), _p("owner")
    archive = await _backup(client, auth_headers)
    resp = await client.post(KEYS, headers=auth_headers, json={
        "name": key, "namespaces": [prefix], "reserve": [{"prefix": prefix}],
    })
    assert resp.status_code == 201, resp.text
    assert await _reserved(prefix) is not None

    report = await _mirror(client, auth_headers, archive)
    assert prefix in report["reservations"]["deleted"]
    assert await _reserved(prefix) is None
    assert key not in {k["name"] for k in (await _backup(client, auth_headers))["api_keys"]}


@pytest.mark.asyncio
async def test_the_namespace_restore_after_such_a_mirror_keeps_its_archived_id(
    client, auth_headers, install_reserves,  # noqa: F811
):
    """The flow the case was filed for, through the backup and mirror: the
    install's reservation travels without an id, so nothing on the target pins
    another one and the restore keeps the archived id."""
    prefix, archived = _p("kb"), str(uuid.uuid4())
    await install_reserves(prefix)
    archive = await _backup(client, auth_headers)

    await RuntimeReservation.get_motor_collection().delete_many({"prefix": prefix})
    report = await _mirror(client, auth_headers, archive)
    assert _skipped(report, "reservations")[prefix] == "managed_on_source"

    restored = await client.put(f"{NS}/{prefix}", headers=auth_headers, json={"namespace_id": archived})
    assert restored.status_code == 200, restored.text
    assert restored.json()["namespace_id"] == archived
