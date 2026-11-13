"""A backup carries the template versions a carried template pins as nested
templates (CASE-1101, reopened).

The leave-out rule followed document pins and `extends`, but not a field's
nested pin: `template_ref` + `template_ref_version` (an object field) or
`array_template_ref` + `array_template_ref_version` (an array of objects).
Nested validation reads exactly that version, so an archive that carried the
parent without it restored documents whose nested objects were silently left
unvalidated. Now a carried version keeps every version it pins, transitively;
a pin with no version (a draft's) keeps every version of its template, and a
draft's ref may still be the submitted value rather than the template_id.
"""

from __future__ import annotations

import uuid

import pytest
from wip_archive.archive import ArchiveReader

from document_store.services.backup_engine import COLLECTION_MAP, DirectBackupEngine


def _template(ns: str, tid: str, version: int, status: str, *fields: dict) -> dict:
    return {
        "template_id": tid, "namespace": ns, "value": tid, "version": version,
        "status": status, "fields": [{"name": "title", "type": "string"}, *fields],
    }


def _object(ref: str, version: int | None) -> dict:
    return {"name": f"obj_{ref}", "type": "object", "template_ref": ref, "template_ref_version": version}


def _array(ref: str, version: int | None) -> dict:
    return {
        "name": f"arr_{ref}", "type": "array", "array_item_type": "object",
        "array_template_ref": ref, "array_template_ref_version": version,
    }


@pytest.fixture
async def seeded(session_mongo_client):
    ns = f"itv-nest-{uuid.uuid4().hex[:8]}"
    db_t, coll_t = COLLECTION_MAP["templates"]
    await session_mongo_client[db_t][coll_t].insert_many([
        # P (active) pins N1 v1 as an object and N2 v1 as array items.
        _template(ns, "P", 1, "active", _object("N1", 1), _array("N2", 1)),
        _template(ns, "N1", 1, "inactive", _object("N4", 1)),  # and N1 v1 pins N4 v1
        _template(ns, "N1", 2, "active"),
        _template(ns, "N2", 1, "inactive"),
        _template(ns, "N2", 2, "active"),
        _template(ns, "N4", 1, "inactive"),
        _template(ns, "N4", 2, "active"),
        # A draft names N3 by its value, with no version yet: every version stays.
        _template(ns, "DRAFT", 1, "draft", _object("N3", None)),
        _template(ns, "N3", 1, "inactive"),
        _template(ns, "N3", 2, "active"),
        # Nothing carried needs N5 v1.
        _template(ns, "N5", 1, "inactive"),
        _template(ns, "N5", 2, "active"),
        # X v1 is left out, so its own pin keeps nothing.
        _template(ns, "X", 1, "inactive", _object("N6", 1)),
        _template(ns, "X", 2, "active"),
        _template(ns, "N6", 1, "inactive"),
        _template(ns, "N6", 2, "active"),
    ])
    yield ns
    for db_name, coll_name in COLLECTION_MAP.values():
        await session_mongo_client[db_name][coll_name].delete_many({"namespace": ns})


async def _carried(mongo, ns: str, path) -> set[tuple[str, int]]:
    await DirectBackupEngine(mongo, None, lambda _e: None).run_backup([ns], path)
    with ArchiveReader(path) as reader:
        return {
            (r["template_id"], r["version"])
            for r in reader.read_entities("templates", namespace=ns)
        }


@pytest.mark.asyncio
async def test_nested_pins_keep_the_versions_they_name(session_mongo_client, seeded, tmp_path):
    carried = await _carried(session_mongo_client, seeded, tmp_path / "a.zip")

    assert ("N1", 1) in carried, "an object field's pinned version was left out"
    assert ("N2", 1) in carried, "an array field's pinned version was left out"
    assert ("N4", 1) in carried, "a pin of a carried pinned version was not followed"


@pytest.mark.asyncio
async def test_a_drafts_value_form_pin_without_a_version_keeps_every_version(
    session_mongo_client, seeded, tmp_path,
):
    carried = await _carried(session_mongo_client, seeded, tmp_path / "a.zip")

    assert {("N3", 1), ("N3", 2)} <= carried


@pytest.mark.asyncio
async def test_unneeded_inactive_versions_are_still_left_out(session_mongo_client, seeded, tmp_path):
    carried = await _carried(session_mongo_client, seeded, tmp_path / "a.zip")

    assert ("N5", 1) not in carried
    assert ("X", 1) not in carried
    assert ("N6", 1) not in carried, "a pin from a left-out version kept its target"
