"""A backup carries the template versions a carried template pins as nested
templates (CASE-1101, reopened).

The leave-out rule followed document pins and `extends`, but not a field's
nested pin: `template_ref` + `template_ref_version` (an object field) or
`array_template_ref` + `array_template_ref_version` (an array of objects).
Nested validation reads exactly that version, so with the parent carried
without it every later write to its documents was refused
(template_not_found). Now a carried version keeps every version it pins,
transitively; a pin with no version (only a row stored before pins were
required has one) keeps every version of its template, and a stored ref (an
extends, too) may be the submitted value or NS:VALUE rather than the
template_id. A malformed legacy row never stops the backup.
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
        # A legacy row pins N3 with no version: every version stays.
        _template(ns, "LEGACY", 1, "draft", _object("N3", None)),
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
async def test_a_pin_without_a_version_keeps_every_version(
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



def _named(ns: str, tid: str, value: str, version: int, status: str, *fields: dict, **extra) -> dict:
    row = _template(ns, tid, version, status, *fields)
    return {**row, "value": value, **extra}


@pytest.mark.asyncio
async def test_value_and_ns_value_refs_and_a_value_form_extends_resolve(
    session_mongo_client, tmp_path,
):
    """f78f9fca R1 and R3: a ref stored as the template's value or as
    NS:VALUE, extends included, names the template whose value it is."""
    ns, other = f"itv-val-{uuid.uuid4().hex[:8]}", f"itv-oth-{uuid.uuid4().hex[:8]}"
    db_t, coll_t = COLLECTION_MAP["templates"]
    rows = [
        _named(ns, "TID-OBJ", "OBJ_V", 1, "inactive"),
        _named(ns, "TID-OBJ", "OBJ_V", 2, "active"),
        _named(other, "TID-FAR", "FAR_V", 1, "inactive"),
        _named(other, "TID-FAR", "FAR_V", 2, "active"),
        _named(ns, "TID-PAR", "PAR_V", 1, "inactive"),
        _named(ns, "TID-PAR", "PAR_V", 2, "active"),
        _named(ns, "TID-USE", "USE_V", 1, "active",
               _object("OBJ_V", 1), _array(f"{other}:FAR_V", 1),
               extends="PAR_V", extends_version=1),
    ]
    await session_mongo_client[db_t][coll_t].insert_many(rows)
    try:
        await DirectBackupEngine(session_mongo_client, None, lambda _e: None).run_backup(
            [ns, other], tmp_path / "v.zip",
        )
        with ArchiveReader(tmp_path / "v.zip") as reader:
            carried = {
                (r["template_id"], r["version"])
                for n in (ns, other) for r in reader.read_entities("templates", namespace=n)
            }
    finally:
        for n in (ns, other):
            await session_mongo_client[db_t][coll_t].delete_many({"namespace": n})
    assert ("TID-OBJ", 1) in carried, "a value-form nested pin was not resolved"
    assert ("TID-FAR", 1) in carried, "an NS:VALUE nested pin was not resolved"
    assert ("TID-PAR", 1) in carried, "a value-form extends was not resolved"


@pytest.mark.asyncio
async def test_a_malformed_legacy_row_does_not_stop_the_backup(session_mongo_client, tmp_path):
    """f78f9fca R5: ref slots are read raw; a non-string ref, a non-dict
    field or a dict where fields belong is skipped, as the referent
    collector skips them."""
    ns = f"itv-bad-{uuid.uuid4().hex[:8]}"
    db_t, coll_t = COLLECTION_MAP["templates"]
    await session_mongo_client[db_t][coll_t].insert_many([
        {**_template(ns, "B1", 1, "active"), "fields": [{"name": "x", "template_ref": 7}, "junk"]},
        {**_template(ns, "B2", 1, "active"), "fields": {"x": {"template_ref": "B1"}}},
        {**_template(ns, "B3", 1, "active"), "extends": 42, "extends_version": 1},
    ])
    try:
        carried = await _carried(session_mongo_client, ns, tmp_path / "b.zip")
    finally:
        await session_mongo_client[db_t][coll_t].delete_many({"namespace": ns})
    assert {("B1", 1), ("B2", 1), ("B3", 1)} <= carried


async def _warnings(mongo, ns: str, path) -> list[str]:
    events: list = []
    await DirectBackupEngine(mongo, None, events.append).run_backup([ns], path)
    return [e.message for e in events if e.phase == "warning"]


@pytest.mark.asyncio
async def test_an_unreadable_row_beside_a_left_out_version_is_a_warning(
    session_mongo_client, tmp_path,
):
    """f78f9fca R6 (CASE-1347): an unreadable ref slot pins nothing the scan
    can follow, so a version it depends on may be left out. When the
    namespace leaves something out, the backup says which carried rows it
    could not read."""
    ns = f"itv-warn-{uuid.uuid4().hex[:8]}"
    db_t, coll_t = COLLECTION_MAP["templates"]
    await session_mongo_client[db_t][coll_t].insert_many([
        {**_template(ns, "B1", 1, "active"), "fields": [{"name": "x", "template_ref": 7}]},
        {**_template(ns, "B2", 1, "active"), "fields": {"x": {"template_ref": "C"}}},
        {**_template(ns, "B3", 1, "active"), "extends": 42, "extends_version": 1},
        _template(ns, "B4", 1, "active", _object("C", "1")),  # a version that is not an int
        _template(ns, "OK", 1, "active", _object("C", 2)),
        _template(ns, "C", 1, "inactive"),  # pinned by nothing readable: left out
        _template(ns, "C", 2, "active"),
    ])
    try:
        warnings = await _warnings(session_mongo_client, ns, tmp_path / "w.zip")
    finally:
        await session_mongo_client[db_t][coll_t].delete_many({"namespace": ns})
    [warning] = warnings
    assert warning.startswith(f"[{ns}] 4 carried template version(s)"), warning
    assert "(B1 v1, B2 v1, B3 v1, B4 v1)" in warning
    assert "include_inactive_templates" in warning


@pytest.mark.asyncio
async def test_an_unreadable_row_with_nothing_left_out_is_not_a_warning(
    session_mongo_client, tmp_path,
):
    """Nothing is left out, so nothing can be missing: no warning."""
    ns = f"itv-quiet-{uuid.uuid4().hex[:8]}"
    db_t, coll_t = COLLECTION_MAP["templates"]
    await session_mongo_client[db_t][coll_t].insert_many([
        {**_template(ns, "B1", 1, "active"), "fields": [{"name": "x", "template_ref": 7}]},
        _template(ns, "C", 1, "active"),
    ])
    try:
        warnings = await _warnings(session_mongo_client, ns, tmp_path / "q.zip")
    finally:
        await session_mongo_client[db_t][coll_t].delete_many({"namespace": ns})
    assert warnings == []
