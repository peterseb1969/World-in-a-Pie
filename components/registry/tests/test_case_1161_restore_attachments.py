"""An attachment restore that loses to a concurrent attach, pinned before
_restore_attachments' refactor.

Provenance: CASE-1161, CASE-1268. The suite did not reach these branches: the
restore inserts an attachment the target did not hold when it was read, and
another request attaches the same name first (duplicate key), or it replaces
one that another request deleted and attached again under the same name; and
an archived upstream that is not a valid URL. A lost write moves the item
from created/replaced (and unresolved) to a concurrent_write skip, an invalid
upstream is an upstream_invalid skip, and the restore goes on with the rest.
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

LOST = ("concurrent_write", "attached by another request during the restore")


def _skips(out: dict) -> list[tuple[str, str, str]]:
    return [(s["id"], s["reason_code"], s["reason"]) for s in out["skipped"]]


@pytest.mark.asyncio
async def test_an_insert_that_loses_to_a_concurrent_attach_is_skipped(fresh, auth_headers, policy, fake_dns, monkeypatch):  # noqa: F811
    client = fresh
    # "later" does not resolve: the restore lists it unresolved before the
    # insert, so losing the insert must take it off that list too.
    await _attach(client, auth_headers, _item("later", "https://later.apps.example"), _item("bb", "http://bb.apps.example:8080"))
    archive = await _backup(client, auth_headers)
    await AppAttachment.delete_all()

    real_insert = AppAttachment.insert

    async def insert(self, *a, **kw):
        if self.name == "later":
            raise DuplicateKeyError("E11000 duplicate key: name later")
        return await real_insert(self, *a, **kw)

    monkeypatch.setattr(AppAttachment, "insert", insert)
    report = await _restore(client, auth_headers, archive, dry_run=False)
    out = report["attachments"]
    assert out["created"] == ["bb"]
    assert out["unresolved"] == []
    assert _skips(out) == [("later", *LOST)]
    assert [a.name for a in await AppAttachment.find_all().to_list()] == ["bb"]


@pytest.mark.asyncio
async def test_a_replace_that_finds_the_name_attached_again_is_skipped(fresh, auth_headers, policy, fake_dns, monkeypatch):  # noqa: F811
    client = fresh
    await _attach(client, auth_headers, _item("aa", "https://aa.apps.example"))
    archive = await _backup(client, auth_headers)
    await _attach(client, auth_headers, _item("aa", "https://moved.example"))
    original = await AppAttachment.find_one({"name": "aa"})
    assert original is not None

    real_save = AppAttachment.save

    async def save(self, *a, **kw):
        # Another request deleted "aa" and attached the name again (a new id)
        # between the restore's read and its write.
        coll = AppAttachment.get_motor_collection()
        doc = await coll.find_one({"_id": self.id})
        await coll.delete_one({"_id": self.id})
        await coll.insert_one({**doc, "_id": "attached-again", "upstream": "https://bb.apps.example"})
        return await real_save(self, *a, **kw)

    monkeypatch.setattr(AppAttachment, "save", save)
    report = await _restore(client, auth_headers, archive, dry_run=False, overwrite=True)
    out = report["attachments"]
    assert out["replaced"] == []
    assert _skips(out) == [("aa", *LOST)]
    held = await AppAttachment.get_motor_collection().find_one({"name": "aa"})
    assert held["_id"] == "attached-again" and held["upstream"] == "https://bb.apps.example"


@pytest.mark.asyncio
async def test_an_archived_upstream_that_is_not_a_valid_url_is_skipped(fresh, auth_headers, policy, fake_dns):  # noqa: F811
    client = fresh
    await _attach(client, auth_headers, _item("aa", "https://aa.apps.example"))
    archive = await _backup(client, auth_headers)
    archive["attachments"].append(dict(archive["attachments"][0], name="pathy", upstream="https://aa.apps.example/api"))
    await AppAttachment.delete_all()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    out = report["attachments"]
    assert out["created"] == ["aa"]
    assert [(s["id"], s["reason_code"]) for s in out["skipped"]] == [("pathy", "upstream_invalid")]
    assert {a.name for a in await AppAttachment.find_all().to_list()} == {"aa"}
