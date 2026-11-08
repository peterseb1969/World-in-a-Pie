"""A mirror leaves a key file's grants alone, pinned before _plan_mirror's
refactor.

Provenance: CASE-1161. The suite never reached this branch of the mirror
plan: a grant the archive does not hold, recorded without managed_by, whose
subject is a key the install's key file manages with its grants. The file
owns that grant, so a mirror does not remove it; the same grant to a key
whose grants the file does not manage is removed.
"""

import pytest

from registry.models.api_key import StoredAPIKey
from registry.models.grant import NamespaceGrant
from tests.test_case_1056_mirror_restore import _backup, _p, _restore, fresh  # noqa: F401


@pytest.mark.asyncio
async def test_a_grant_owned_by_the_key_file_is_kept_and_one_it_does_not_own_is_removed(fresh, auth_headers):  # noqa: F811
    client = fresh
    archive = await _backup(client, auth_headers)

    owned, unowned = _p("file-grants"), _p("file-only")
    await StoredAPIKey(
        name=owned, key_hash="$2b$12$x", managed_by="wip-deploy", origin="file", grants_managed=True,
    ).insert()
    await StoredAPIKey(
        name=unowned, key_hash="$2b$12$y", managed_by="wip-deploy", origin="file", grants_managed=False,
    ).insert()
    for subject in (owned, unowned):
        await NamespaceGrant(
            namespace="vendor1", subject=subject, subject_type="api_key", permission="read",
            granted_by="someone",
        ).insert()

    dry = await _restore(client, auth_headers, archive, mode="mirror")
    assert dry["grants"]["deleted"] == [f"vendor1/api_key:{unowned}"]
    assert dry["api_keys"]["deleted"] == []
