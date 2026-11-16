"""A grant restore meeting a grant orphaned here, pinned before
_restore_grants' refactor.

Provenance: CASE-1161. The suite never reached this branch: the archive
holds an effective grant, this instance holds the same grant orphaned, and
the restore does not overwrite. The grant is left as it is and reported
orphaned_here, with how to resolve it; with overwrite it is replaced. Each
run is applied, and the stored grant is read back after it.
"""

import pytest

from registry.models.grant import NamespaceGrant
from tests.test_case_1056_mirror_restore import _backup, _grant, _restore, fresh  # noqa: F401


@pytest.mark.asyncio
async def test_a_grant_orphaned_here_is_left_unless_overwritten(fresh, auth_headers):  # noqa: F811
    client = fresh
    await _grant(client, auth_headers, "default", "team-1161-orph", permission="write")
    archive = await _backup(client, auth_headers)
    stored = await NamespaceGrant.find_one({"subject": "team-1161-orph"})
    await stored.set({"orphaned": True})

    async def stored_grant() -> NamespaceGrant:
        return await NamespaceGrant.find_one({"subject": "team-1161-orph"})

    kept = await _restore(client, auth_headers, archive, dry_run=False)
    skipped = [(s["id"], s["reason_code"]) for s in kept["grants"]["skipped"]]
    assert ("default/group:team-1161-orph", "orphaned_here") in skipped
    reason = next(s["reason"] for s in kept["grants"]["skipped"] if s["reason_code"] == "orphaned_here")
    assert reason == (
        "orphaned on this instance; activate it on the namespace, or pass overwrite to replace it"
    )
    # Left as it is: still the orphaned grant this instance held.
    left = await stored_grant()
    assert (left.id, left.orphaned, left.permission) == (stored.id, True, "write")

    over = await _restore(client, auth_headers, archive, mode="overwrite", dry_run=False)
    assert "default/group:team-1161-orph" in over["grants"]["replaced"]
    # Replaced with the archive's effective grant.
    replaced = await stored_grant()
    assert (replaced.orphaned, replaced.permission) == (False, "write")
