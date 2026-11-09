"""System restore: a legacy `apikey:<name>` grant subject is read as the
bare key name; the caller's key is protected only from a replacement the
restore would make; a salt is adopted only while the stack is still fresh;
a record another request writes during the restore is skipped and listed,
and the restore goes on.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from pymongo.errors import DuplicateKeyError

from registry.models.api_key import StoredAPIKey
from registry.models.grant import NamespaceGrant
from registry.services import attachments as attach_service
from registry.services import key_shadowing, system_backup
from registry.services.system_backup import SaltMismatch, SystemArchive
from tests.test_case_1056_mirror_restore import _backup, _key, _p, _restore


@pytest.fixture
async def fresh(client, tmp_path, monkeypatch):
    """A client over a stack with no grants and no shadow state."""
    await NamespaceGrant.delete_all()
    key_shadowing.reset_for_tests()
    path = tmp_path / "attach-policy.json"
    path.write_text(json.dumps({"blocked_hosts": [], "reserved_names": []}))
    monkeypatch.setenv(attach_service.ATTACH_POLICY_FILE_ENV, str(path))
    yield client
    key_shadowing.reset_for_tests()


def _archive_grant(subject, permission="read", **extra):
    return {"namespace": "default", "subject": subject, "subject_type": "api_key",
            "permission": permission, "namespace_id": None, **extra}


def _archive(grants):
    return SystemArchive.model_validate({
        "created_at": "2026-11-01T00:00:00Z",
        "source": {"salt_fingerprint": "sha256:x"},
        "grants": grants,
    })


def test_a_legacy_key_subject_is_read_as_the_bare_name():
    (g,) = _archive([_archive_grant("apikey:ci")]).grants
    assert g.subject == "ci"


def test_two_spellings_of_one_grant_collapse_to_the_higher_permission():
    archive = _archive([_archive_grant("apikey:ci", "write"), _archive_grant("ci", "read")])
    assert [(g.subject, g.permission) for g in archive.grants] == [("ci", "write")]


def test_a_user_subject_keeps_its_spelling():
    archive = _archive([{**_archive_grant("apikey:someone"), "subject_type": "user"}])
    assert archive.grants[0].subject == "apikey:someone"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["overwrite", "mirror"])
async def test_the_callers_key_is_not_refused_when_the_restore_would_not_replace_it(fresh, auth_headers, mode):
    """A scoped archive holds an in-scope key of the caller's name; here the
    caller's key is stack-wide, so the restore leaves it alone
    (out_of_scope_here) and has nothing to refuse."""
    client = fresh
    name, plaintext = await _key(client, auth_headers, name=_p("ops"), groups=["wip-admins"], namespaces=None)
    archive = await _backup(client, auth_headers)
    archive["source"]["scope"] = {"namespaces": ["default"], "attachments": [], "keys_left_out": []}
    archived = next(k for k in archive["api_keys"] if k["name"] == name)
    archived.update(groups=[], namespaces=["default"])
    archive["api_keys"] = [archived]
    archive["grants"] = [g for g in archive["grants"] if g["namespace"] == "default"]
    archive["reservations"], archive["attachments"] = [], []

    dry = await _restore(client, {"X-API-Key": plaintext}, archive, mode=mode)
    assert dry["refused"] == [], dry["refused"]
    assert [(s["id"], s["reason_code"]) for s in dry["api_keys"]["skipped"]] == [(name, "out_of_scope_here")]


@pytest.mark.asyncio
async def test_a_salt_is_not_adopted_once_the_stack_holds_a_key_of_its_own(fresh, auth_headers):
    """Freshness is decided again where the salt is adopted: a key created
    under the stack's salt in between would otherwise never authenticate."""
    client = fresh
    archive = SystemArchive.model_validate(await _backup(client, auth_headers))
    await _key(client, auth_headers)  # a Console key of this stack's own
    with patch.object(system_backup, "_salt_to_adopt", AsyncMock(return_value=("other-salt", None))), \
            patch.object(system_backup, "adopt_salt", AsyncMock()) as adopted:
        archive.source.salt_fingerprint = system_backup.salt_fingerprint("other-salt")
        with pytest.raises(SaltMismatch):
            await system_backup.restore_archive(archive, dry_run=False)
    adopted.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_key_written_meanwhile_is_skipped_and_the_restore_goes_on(fresh, auth_headers):
    client = fresh
    first, _ = await _key(client, auth_headers)
    second, _ = await _key(client, auth_headers)
    archive = await _backup(client, auth_headers)
    for name in (first, second):
        assert (await client.delete(f"/api/registry/api-keys/{name}", headers=auth_headers)).status_code == 200

    real_insert = StoredAPIKey.insert

    async def insert(self, *args, **kwargs):
        if self.name == first:
            raise DuplicateKeyError("E11000 duplicate key: name")
        return await real_insert(self, *args, **kwargs)

    with patch.object(StoredAPIKey, "insert", insert):
        report = await _restore(client, auth_headers, archive, dry_run=False)
    keys = report["api_keys"]
    assert first not in keys["created"]
    assert (first, "concurrent_write") in [(s["id"], s["reason_code"]) for s in keys["skipped"]]
    assert second in keys["created"]
