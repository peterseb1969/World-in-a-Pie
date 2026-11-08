"""A backup leaves out inactive template versions nothing needs (CASE-1101).

By default an archive carries every template version that is not inactive
(drafts included), plus every inactive version something in the archive
still needs: one an exported document is pinned to, one a carried template
extends, and — so a template never vanishes from the archive while other
definitions and its Registry entry name it — the highest version of a
template whose versions are all inactive. include_inactive_templates=true
keeps every version.

A merge no longer refuses a template over its status alone: a retired
template whose schema matches the target's is the same definition.
"""

from __future__ import annotations

import uuid

import pytest
from wip_archive.archive import ArchiveReader

from document_store.services.backup_engine import COLLECTION_MAP, DirectBackupEngine
from document_store.services.merge_definitions import DefinitionsPlanner


def _template(ns: str, tid: str, version: int, status: str, value: str | None = None, **extra) -> dict:
    return {
        "template_id": tid, "namespace": ns, "value": value or tid, "version": version,
        "status": status, "fields": [{"name": "title", "type": "string"}], **extra,
    }


def _document(ns: str, did: str, tid: str, version: int, status: str = "active") -> dict:
    return {
        "document_id": did, "namespace": ns, "version": 1, "status": status,
        "template_id": tid, "template_version": version, "data": {"title": did},
    }


@pytest.fixture
async def seeded(session_mongo_client):
    ns = f"itv-{uuid.uuid4().hex[:8]}"
    db_t, coll_t = COLLECTION_MAP["templates"]
    db_d, coll_d = COLLECTION_MAP["documents"]
    templates = [
        _template(ns, "T1", 1, "inactive"),               # nothing needs it
        _template(ns, "T1", 2, "inactive"),               # an archived document pins it
        _template(ns, "T1", 3, "active"),
        _template(ns, "T2", 1, "inactive"),               # all versions inactive:
        _template(ns, "T2", 2, "inactive"),               #   the highest stays
        _template(ns, "T3", 1, "inactive"),               # T4 extends it
        _template(ns, "T3", 2, "active"),
        _template(ns, "T4", 1, "active", extends="T3", extends_version=1),
        _template(ns, "T5", 1, "draft"),
    ]
    await session_mongo_client[db_t][coll_t].insert_many(templates)
    await session_mongo_client[db_d][coll_d].insert_many([
        _document(ns, "D1", "T1", 2, status="archived"),
        _document(ns, "D2", "T1", 3),
    ])
    yield ns
    for db_name, coll_name in COLLECTION_MAP.values():
        await session_mongo_client[db_name][coll_name].delete_many({"namespace": ns})


async def _backup(mongo, ns: str, path, **options) -> tuple[set[tuple[str, int]], int, list]:
    events: list = []
    await DirectBackupEngine(mongo, None, events.append).run_backup([ns], path, **options)
    with ArchiveReader(path) as reader:
        rows = list(reader.read_entities("templates", namespace=ns))
        counted = reader.read_manifest().namespaces[0].counts.templates
    return {(r["template_id"], r["version"]) for r in rows}, counted, events


@pytest.mark.asyncio
async def test_a_backup_leaves_out_the_inactive_versions_nothing_needs(
    session_mongo_client, seeded, tmp_path,
):
    carried, counted, events = await _backup(session_mongo_client, seeded, tmp_path / "a.zip")

    assert carried == {
        ("T1", 2), ("T1", 3), ("T2", 2), ("T3", 1), ("T3", 2), ("T4", 1), ("T5", 1),
    }
    assert counted == len(carried)
    notes = [e.message for e in events if "inactive template version" in e.message]
    assert notes and "2" in notes[0], notes


@pytest.mark.asyncio
async def test_include_inactive_templates_keeps_every_version(session_mongo_client, seeded, tmp_path):
    carried, counted, _ = await _backup(
        session_mongo_client, seeded, tmp_path / "a.zip", include_inactive_templates=True,
    )

    assert len(carried) == 9 and counted == 9


@pytest.mark.asyncio
async def test_without_documents_their_pins_do_not_carry_a_version(
    session_mongo_client, seeded, tmp_path,
):
    carried, _, _ = await _backup(
        session_mongo_client, seeded, tmp_path / "a.zip", skip_documents=True,
    )

    assert ("T1", 2) not in carried
    assert ("T1", 3) in carried


# ── Merge: status alone never refuses a template ─────────────────────────


@pytest.fixture
async def merge_ns(session_mongo_client):
    ns = f"itv-merge-{uuid.uuid4().hex[:8]}"
    yield ns
    for db_name, coll_name in COLLECTION_MAP.values():
        await session_mongo_client[db_name][coll_name].delete_many({"namespace": ns})


async def _plan(mongo, ns: str, archive_template: dict, target_template: dict):
    db_t, coll_t = COLLECTION_MAP["templates"]
    await mongo[db_t][coll_t].insert_one(dict(target_template))
    return await DefinitionsPlanner(mongo, COLLECTION_MAP).plan(
        {"templates": [archive_template]}, ns,
    )


@pytest.mark.asyncio
async def test_a_retired_template_with_the_same_schema_does_not_refuse(session_mongo_client, merge_ns):
    plan = await _plan(
        session_mongo_client, merge_ns,
        _template(merge_ns, "A-tpl", 1, "inactive", value="BOOT"),
        _template(merge_ns, "B-tpl", 1, "active", value="BOOT"),
    )

    assert plan.compatible, plan.incompatibilities
    assert plan.mapping["templates"] == {"A-tpl": "B-tpl"}


@pytest.mark.asyncio
async def test_a_retired_template_with_a_different_schema_still_refuses(session_mongo_client, merge_ns):
    other = _template(merge_ns, "B-tpl", 1, "active", value="BOOT")
    other["fields"] = [{"name": "title", "type": "integer"}]

    plan = await _plan(
        session_mongo_client, merge_ns, _template(merge_ns, "A-tpl", 1, "inactive", value="BOOT"), other,
    )

    assert not plan.compatible
    (issue,) = plan.incompatibilities
    assert "status" not in str(issue)
