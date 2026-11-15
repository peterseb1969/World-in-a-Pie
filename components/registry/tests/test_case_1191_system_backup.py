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


@pytest.mark.parametrize("dead", [
    {"orphaned": True, "namespace_id": "ns-gone"},
    {"expires_at": "2020-01-01T00:00:00Z"},
], ids=["orphaned", "expired"])
@pytest.mark.parametrize("order", ["dead_first", "dead_last"])
def test_the_spelling_that_gives_access_wins_over_a_higher_dead_one(dead, order):
    # An orphaned or expired admin spelling must not replace the read grant
    # the key actually held: the restored key would lose its access.
    dead_admin = _archive_grant("apikey:ci", "admin", **dead)
    live_read = _archive_grant("ci", "read")
    grants = [dead_admin, live_read] if order == "dead_first" else [live_read, dead_admin]
    (g,) = _archive(grants).grants
    assert (g.permission, g.orphaned, g.expires_at) == ("read", False, None)


def test_between_two_dead_spellings_the_higher_permission_wins():
    (g,) = _archive([
        _archive_grant("ci", "read", expires_at="2020-01-01T00:00:00Z"),
        _archive_grant("apikey:ci", "write", orphaned=True, namespace_id="ns-gone"),
    ]).grants
    assert g.permission == "write"


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


@pytest.mark.asyncio
async def test_a_record_removed_during_the_restore_is_skipped_not_replaced(fresh, auth_headers):
    """A plain set on a record another request deleted matches nothing:
    the restore must list it as skipped, and not hand the key to the key
    provider as replaced."""
    client = fresh
    name, _ = await _key(client, auth_headers, description="before")
    archive = await _backup(client, auth_headers)
    next(k for k in archive["api_keys"] if k["name"] == name)["description"] = "archived"

    real = system_backup._set_if_present

    async def removed_first(doc, fields):
        if getattr(doc, "name", None) == name:
            await StoredAPIKey.find_one({"name": name}).delete()
        return await real(doc, fields)

    with patch.object(system_backup, "_set_if_present", removed_first):
        report = await _restore(client, auth_headers, archive, dry_run=False, overwrite=True)
    keys = report["api_keys"]
    assert name not in keys["replaced"]
    skipped = {s["id"]: (s["reason_code"], s["reason"]) for s in keys["skipped"]}
    assert skipped[name][0] == "concurrent_write"
    assert "removed by another request" in skipped[name][1]
    assert await StoredAPIKey.find_one({"name": name}) is None


@pytest.mark.asyncio
async def test_set_if_present_reports_a_record_that_is_gone(fresh, auth_headers):
    name, _ = await _key(fresh, auth_headers)
    doc = await StoredAPIKey.find_one({"name": name})
    assert await system_backup._set_if_present(doc, {"description": "kept"}) is True
    assert (await StoredAPIKey.find_one({"name": name})).description == "kept"
    await StoredAPIKey.find_one({"name": name}).delete()
    assert await system_backup._set_if_present(doc, {"description": "lost"}) is False
