"""What a backup says it left out, and what a merge counts as a difference
(CASE-1275, batch 14 of the commit-review backfill).

- The manifest stamps `include_all_versions: true` only when the archive
  really holds every version: not when documents were skipped, and not when
  inactive template versions nothing needs were left out.
- The left-out count rides on the job record (the complete event's details),
  not only on a progress line that is gone once the job is.
- A merge treats a status difference as cosmetic for retirements only: an
  archive definition that is `draft` where the target is active (or the
  reverse) refuses the pair, because a draft was never activated.
- The pure-unit Mongo fake answers the reads the leave-out scan makes, so a
  unit run of `run_backup` exercises it instead of silently seeing nothing.
"""

from __future__ import annotations

import uuid

import pytest
from wip_archive.archive import ArchiveReader

from document_store.services.backup_engine import (
    BACKUP_ENTITY_ORDER,
    COLLECTION_MAP,
    DirectBackupEngine,
    _backup_job_result,
)
from document_store.services.merge_definitions import DefinitionsPlanner
from tests.test_backup_engine import _make_mongo_mock
from tests.test_case_1101_inactive_template_versions import _document, _template


def _rows(ns: str) -> list[dict]:
    return [
        _template(ns, "T1", 1, "inactive"),   # nothing needs it → left out
        _template(ns, "T1", 2, "active"),     # a document pins it
        _template(ns, "T2", 1, "inactive"),   # left out: the highest stays
        _template(ns, "T2", 2, "inactive"),   # highest of an all-inactive template
    ]


@pytest.fixture
async def seeded(session_mongo_client):
    ns = f"c1275-{uuid.uuid4().hex[:8]}"
    db_t, coll_t = COLLECTION_MAP["templates"]
    db_d, coll_d = COLLECTION_MAP["documents"]
    await session_mongo_client[db_t][coll_t].insert_many(_rows(ns))
    await session_mongo_client[db_d][coll_d].insert_many([_document(ns, "D1", "T1", 2)])
    yield ns
    for db_name, coll_name in COLLECTION_MAP.values():
        await session_mongo_client[db_name][coll_name].delete_many({"namespace": ns})


async def _backup(mongo, ns: str, path, **options):
    events: list = []
    await DirectBackupEngine(mongo, None, events.append).run_backup([ns], path, **options)
    with ArchiveReader(path) as reader:
        return reader.read_manifest(), events


@pytest.mark.asyncio
async def test_full_fidelity_is_stamped_only_when_the_archive_holds_every_version(
    session_mongo_client, seeded, tmp_path,
):
    default, _ = await _backup(session_mongo_client, seeded, tmp_path / "default.zip")
    assert default.include_all_versions is False
    assert {(x["template_id"], x["version"]) for x in default.namespaces[0].template_versions_left_out} == {
        ("T1", 1), ("T2", 1),
    }

    everything, _ = await _backup(
        session_mongo_client, seeded, tmp_path / "all.zip", include_inactive_templates=True,
    )
    assert everything.include_all_versions is True
    assert everything.namespaces[0].template_versions_left_out is None

    without_docs, _ = await _backup(
        session_mongo_client, seeded, tmp_path / "defs.zip", skip_documents=True,
    )
    assert without_docs.include_all_versions is False


@pytest.mark.asyncio
async def test_the_complete_event_carries_the_count_onto_the_job_record(
    session_mongo_client, seeded, tmp_path,
):
    _manifest, events = await _backup(session_mongo_client, seeded, tmp_path / "a.zip")

    (complete,) = [e for e in events if e.phase == "complete"]
    assert complete.details == {
        "mode": "backup",
        "template_versions_left_out": 2,
        "namespaces": {seeded: {"template_versions_left_out": 2}},
    }

    _manifest, events = await _backup(
        session_mongo_client, seeded, tmp_path / "b.zip", include_inactive_templates=True,
    )
    (complete,) = [e for e in events if e.phase == "complete"]
    assert complete.details["template_versions_left_out"] == 0
    assert complete.details["namespaces"] == {}


def test_the_result_names_only_the_namespaces_that_left_something_out():
    result = _backup_job_result(["a", "b"], {"a": [("T", 1)], "b": []})

    assert result == {
        "mode": "backup",
        "template_versions_left_out": 1,
        "namespaces": {"a": {"template_versions_left_out": 1}},
    }


@pytest.mark.asyncio
async def test_a_draft_against_an_active_definition_refuses_the_pair(session_mongo_client):
    ns = f"c1275-draft-{uuid.uuid4().hex[:8]}"
    db_t, coll_t = COLLECTION_MAP["templates"]
    await session_mongo_client[db_t][coll_t].insert_many([
        _template(ns, "B-tpl", 1, "active", value="BOOT"),
        _template(ns, "D-tpl", 1, "draft", value="SLUG"),
    ])
    try:
        planner = DefinitionsPlanner(session_mongo_client, COLLECTION_MAP)
        draft_vs_active = await planner.plan(
            {"templates": [_template(ns, "A-tpl", 1, "draft", value="BOOT")]}, ns,
        )
        retired_vs_active = await planner.plan(
            {"templates": [_template(ns, "A-tpl", 1, "inactive", value="BOOT")]}, ns,
        )
        draft_vs_draft = await planner.plan(
            {"templates": [_template(ns, "C-tpl", 1, "draft", value="SLUG")]}, ns,
        )
    finally:
        await session_mongo_client[db_t][coll_t].delete_many({"namespace": ns})

    assert not draft_vs_active.compatible, draft_vs_active.incompatibilities
    (issue,) = draft_vs_active.incompatibilities
    assert "status" in str(issue)
    assert retired_vs_active.compatible, retired_vs_active.incompatibilities
    assert draft_vs_draft.compatible, draft_vs_draft.incompatibilities


@pytest.mark.asyncio
async def test_the_unit_fake_answers_the_leave_out_scan(tmp_path):
    """The scan reads templates with a bare projected find; a fake that only
    answers find().sort() made a unit run of run_backup claim a scan it never
    saw rows for."""
    rows = _rows("kb")
    mongo, _ = _make_mongo_mock(
        docs_per_collection={"templates": rows},
        counts_per_collection={e: 0 for e in BACKUP_ENTITY_ORDER} | {"templates": len(rows)},
        namespace_config_doc={"prefix": "kb"},
    )

    manifest, events = await _backup(mongo, "kb", tmp_path / "unit.zip")

    assert {(x["template_id"], x["version"])
            for x in manifest.namespaces[0].template_versions_left_out} == {("T1", 1), ("T2", 1)}
    (complete,) = [e for e in events if e.phase == "complete"]
    assert complete.details["template_versions_left_out"] == 2
