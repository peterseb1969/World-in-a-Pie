"""align_templates: what the version search places, and what it may map onto.

The search used to give up on every version as soon as one version could not
be placed under any choice of the others, and fell back to each version's
first candidate, so a consistent mapping for the rest was missed. Every
version may now stay unplaced, as its last option, and the search keeps the
assignment that places the most versions.

A target draft was offered as a place to map onto, and the mapping then
refused because a draft was on one side. A draft was never activated and no
document can be written against it, so it is never a candidate.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.services.backup_engine import COLLECTION_MAP
from document_store.services.merge_definitions import DefinitionsPlanner

NAMESPACE = "align-1409-ns"
AGE = {"name": "age", "type": "integer"}
NOTE = {"name": "note", "type": "string"}


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))

    async def _clear():
        for db_name, coll_name in COLLECTION_MAP.values():
            await client[db_name][coll_name].delete_many({"namespace": NAMESPACE})

    await _clear()
    yield client
    await _clear()
    client.close()


def _tpl(tid: str, value: str, version: int, *fields: dict, **extra) -> dict:
    return {
        "template_id": tid, "namespace": NAMESPACE, "value": value, "label": value,
        "version": version, "status": "active", "identity_fields": ["code"],
        "fields": [{"name": "code", "type": "string"}, *fields], **extra,
    }


def _ref(name: str, template_id: str, version: int) -> dict:
    return {"name": name, "type": "object", "template_ref": template_id,
            "template_ref_version": version}


async def _plan(db, target: list[dict], archive: list[dict]):
    db_name, coll_name = COLLECTION_MAP["templates"]
    await db[db_name][coll_name].insert_many([dict(t) for t in target])
    planner = DefinitionsPlanner(db, COLLECTION_MAP, align_templates=True)
    return await planner.plan({"templates": archive}, NAMESPACE)


def _mapped(plan, archive_id: str) -> dict[int, int | None]:
    return {v: a.target_version for v, a in plan.alignment[archive_id].versions.items()}


@pytest.mark.asyncio
async def test_a_version_unplaceable_under_every_choice_does_not_cost_the_others(db):
    # EPS needs ZETA on v2 and THETA needs it on v3: no choice places both,
    # so one of them stays unplaced. That must not stop ALPHA and BETA from
    # the mapping only a non-preferred choice gives them (ALPHA v1, BETA v5);
    # the old search fell back to ALPHA's preferred v2, which places no BETA.
    plan = await _plan(
        db,
        target=[_tpl("T-a", "ALPHA", 1, _ref("beta", "T-b", 5)),
                _tpl("T-a", "ALPHA", 2, _ref("beta", "T-b", 4)),
                _tpl("T-b", "BETA", 4, _ref("alpha", "T-a", 1)),
                _tpl("T-b", "BETA", 5, _ref("alpha", "T-a", 1)),
                _tpl("T-z", "ZETA", 2, NOTE),
                _tpl("T-z", "ZETA", 3, NOTE),
                _tpl("T-e", "EPS", 1, _ref("z", "T-z", 2)),
                _tpl("T-t", "THETA", 1, _ref("z", "T-z", 3))],
        archive=[_tpl("A-a", "ALPHA", 1, _ref("beta", "A-b", 1)),
                 _tpl("A-b", "BETA", 1, _ref("alpha", "A-a", 1)),
                 _tpl("A-z", "ZETA", 1, NOTE),
                 _tpl("A-e", "EPS", 1, _ref("z", "A-z", 1)),
                 _tpl("A-t", "THETA", 1, _ref("z", "A-z", 1))],
    )

    assert _mapped(plan, "A-a") == {1: 1}
    assert _mapped(plan, "A-b") == {1: 5}
    placed = [_mapped(plan, "A-e")[1], _mapped(plan, "A-t")[1]]
    assert placed.count(None) == 1
    zeta = _mapped(plan, "A-z")[1]
    assert zeta in (2, 3)
    # Whichever of EPS and THETA is placed agrees with ZETA's mapping.
    assert (placed[0] is not None and zeta == 2) or (placed[1] is not None and zeta == 3)


@pytest.mark.asyncio
async def test_a_target_draft_is_never_mapped_onto(db):
    # The target's v2 is a draft with the archive version's exact schema. It
    # used to be the mapping, which the draft rule then refused; the archive
    # version now maps onto the active v1 it is compatible with (its extra
    # field is checked against the arriving documents later).
    plan = await _plan(
        db,
        target=[_tpl("T-d", "DOC", 1, NOTE),
                _tpl("T-d", "DOC", 2, NOTE, AGE, status="draft")],
        archive=[_tpl("A-d", "DOC", 1, NOTE, AGE)],
    )

    assert plan.compatible, plan.incompatibilities
    version = plan.alignment["A-d"].versions[1]
    assert version.target_version == 1
    assert version.removed_fields == ["age"]


@pytest.mark.asyncio
async def test_a_target_holding_only_drafts_places_nothing_and_says_why(db):
    plan = await _plan(
        db,
        target=[_tpl("T-d", "DOC", 1, NOTE, status="draft")],
        archive=[_tpl("A-d", "DOC", 1, NOTE)],
    )

    assert plan.compatible, plan.incompatibilities
    version = plan.alignment["A-d"].versions[1]
    assert version.target_version is None
    assert "only draft versions" in version.reason


async def _settled(db, target: list[dict], archive: list[dict], pinned: set[tuple[str, int]]):
    db_name, coll_name = COLLECTION_MAP["templates"]
    await db[db_name][coll_name].insert_many([dict(t) for t in target])
    planner = DefinitionsPlanner(
        db, COLLECTION_MAP, align_templates=True, defer_alignment=True,
    )
    plan = await planner.plan({"templates": archive}, NAMESPACE)
    planner.settle_alignment(plan, pinned)
    return plan


@pytest.mark.asyncio
async def test_a_draft_no_document_uses_is_history_and_refuses_nothing(db):
    # The archive's v2 is a never-activated draft with the target v1's schema.
    # Judged in pass 1 it refused the whole merge; documents pin only v1.
    plan = await _settled(
        db,
        target=[_tpl("T-d", "DOC", 1, NOTE)],
        archive=[_tpl("A-d", "DOC", 1, NOTE),
                 _tpl("A-d", "DOC", 2, NOTE, status="draft")],
        pinned={("A-d", 1)},
    )

    assert plan.compatible, plan.incompatibilities
    versions = plan.alignment["A-d"].versions
    assert versions[1].target_version == 1 and not versions[1].history
    assert versions[2].history and versions[2].target_version is None
    assert plan.aligned == 1


@pytest.mark.asyncio
async def test_a_draft_documents_use_is_refused(db):
    plan = await _settled(
        db,
        target=[_tpl("T-d", "DOC", 1, NOTE)],
        archive=[_tpl("A-d", "DOC", 1, NOTE, status="draft")],
        pinned={("A-d", 1)},
    )

    (issue,) = plan.incompatibilities
    assert issue.name == "DOC v1" and "draft" in issue.reason


@pytest.mark.asyncio
async def test_a_pinned_version_brings_the_versions_it_references(db):
    # Documents pin only ORDER v1, which names ITEM v1: ITEM v1 is decided
    # too, so ORDER's reference compares in the target's numbering. ITEM v2,
    # which nothing uses or names, stays history even though its schema
    # matches nothing on the target.
    plan = await _settled(
        db,
        target=[_tpl("T-i", "ITEM", 7, NOTE),
                _tpl("T-o", "ORDER", 3, _ref("item", "T-i", 7))],
        archive=[_tpl("A-i", "ITEM", 1, NOTE),
                 _tpl("A-i", "ITEM", 2, AGE),
                 _tpl("A-o", "ORDER", 1, _ref("item", "A-i", 1))],
        pinned={("A-o", 1)},
    )

    assert plan.compatible, plan.incompatibilities
    assert plan.alignment["A-o"].versions[1].target_version == 3
    item = plan.alignment["A-i"].versions
    assert item[1].target_version == 7 and not item[1].history
    assert item[2].history


@pytest.mark.asyncio
async def test_nothing_pinned_leaves_every_version_history(db):
    plan = await _settled(
        db,
        target=[_tpl("T-d", "DOC", 1, NOTE)],
        archive=[_tpl("A-d", "DOC", 1, NOTE, status="draft")],
        pinned=set(),
    )

    assert plan.compatible
    assert plan.alignment["A-d"].versions[1].history
    assert plan.aligned == 0
