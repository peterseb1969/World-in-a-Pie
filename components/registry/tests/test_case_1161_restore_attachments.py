"""An attachment restore that loses to a concurrent attach, pinned before
_restore_attachments' refactor.

Provenance: CASE-1161. The suite never reached this branch: the restore
inserts an attachment the target did not hold when it was read, and another
request attaches the same name first (duplicate key). The item moves from
created to a concurrent_write skip, and the restore goes on with the rest.
"""

import pytest
from pymongo.errors import DuplicateKeyError

from registry.models.attachment import AppAttachment
from tests.test_case_1055_attachment_backup import (  # noqa: F401
    _attach,
    _backup,
    _item,
    _restore,
    fake_dns,
    fresh,
    policy,
)


@pytest.mark.asyncio
async def test_an_insert_that_loses_to_a_concurrent_attach_is_skipped(fresh, auth_headers, policy, monkeypatch):  # noqa: F811
    client = fresh
    await _attach(client, auth_headers, _item("aa", "https://aa.apps.example"), _item("bb", "http://bb.apps.example:8080"))
    archive = await _backup(client, auth_headers)
    await AppAttachment.delete_all()

    real_insert = AppAttachment.insert

    async def insert(self, *a, **kw):
        if self.name == "aa":
            raise DuplicateKeyError("E11000 duplicate key: name aa")
        return await real_insert(self, *a, **kw)

    monkeypatch.setattr(AppAttachment, "insert", insert)
    report = await _restore(client, auth_headers, archive, dry_run=False)
    out = report["attachments"]
    assert out["created"] == ["bb"]
    assert "aa" not in out["unresolved"]
    assert [(s["id"], s["reason_code"], s["reason"]) for s in out["skipped"]] == [
        ("aa", "concurrent_write", "attached by another request during the restore"),
    ]
    assert [a.name for a in await AppAttachment.find_all().to_list()] == ["bb"]
