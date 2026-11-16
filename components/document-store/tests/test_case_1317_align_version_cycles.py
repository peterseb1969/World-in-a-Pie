"""align_templates maps versions that reference each other's versions.

A template version can name another template's version (a field's
template_ref_version, an extends_version), and under align_templates that
number compares only once the other template's versions are mapped. Mapping
one template after another left every version in a cycle unplaceable: each
waited for the other. A template that pins its own version is a cycle of
one. Versions now start from every target version their schema matches with
those references left aside, and keep only the candidates whose references
agree.

A version keeps no mapping the narrowing ruled out: the number it pinned is
another install's, so it may not be matched against the target by it. And
because one version's preferred candidate can block another's mapping, the
choice backs out of it and tries the next one when that happens.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.services.backup_engine import COLLECTION_MAP
from document_store.services.merge_definitions import DefinitionsPlanner

NAMESPACE = "align-1317-ns"
AGE_STR = {"name": "age", "type": "string"}
AGE_INT = {"name": "age", "type": "integer"}
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
async def test_two_templates_that_reference_each_others_versions_are_mapped(db):
    plan = await _plan(
        db,
        target=[_tpl("T-a", "ALPHA", 3, _ref("beta", "T-b", 5)),
                _tpl("T-b", "BETA", 5, _ref("alpha", "T-a", 3))],
        archive=[_tpl("A-a", "ALPHA", 1, _ref("beta", "A-b", 1)),
                 _tpl("A-b", "BETA", 1, _ref("alpha", "A-a", 1))],
    )

    assert plan.compatible, plan.incompatibilities
    assert _mapped(plan, "A-a") == {1: 3}
    assert _mapped(plan, "A-b") == {1: 5}
    assert plan.alignment["A-a"].versions[1].reason == "same schema as target v3"


@pytest.mark.asyncio
async def test_a_template_that_pins_its_own_version_is_mapped(db):
    plan = await _plan(
        db,
        target=[_tpl("T-a", "NODE", 3, _ref("parent", "T-a", 3))],
        archive=[_tpl("A-a", "NODE", 1, _ref("parent", "A-a", 1))],
    )

    assert _mapped(plan, "A-a") == {1: 3}


@pytest.mark.asyncio
async def test_a_cycle_whose_references_disagree_stays_unplaceable(db):
    # The target's ALPHA v3 pins BETA v5, but BETA v5 pins ALPHA v4, which
    # the archive's ALPHA v1 does not match: no mapping is consistent.
    plan = await _plan(
        db,
        target=[_tpl("T-a", "ALPHA", 3, _ref("beta", "T-b", 5)),
                _tpl("T-a", "ALPHA", 4, _ref("beta", "T-b", 5),
                     {"name": "extra", "type": "string", "mandatory": True}),
                _tpl("T-b", "BETA", 5, _ref("alpha", "T-a", 4))],
        archive=[_tpl("A-a", "ALPHA", 1, _ref("beta", "A-b", 1)),
                 _tpl("A-b", "BETA", 1, _ref("alpha", "A-a", 1))],
    )

    assert _mapped(plan, "A-a") == {1: None}
    assert _mapped(plan, "A-b") == {1: None}


@pytest.mark.asyncio
async def test_two_target_versions_differing_only_in_a_pinned_version_pick_the_one_that_agrees(db):
    # BETA v2 and v3 differ only in the ALPHA version they pin. The archive's
    # ALPHA v1 matches the target's ALPHA v1, so BETA v1 must map onto v2,
    # not onto the preferred (highest) v3.
    plan = await _plan(
        db,
        target=[_tpl("T-a", "ALPHA", 1),
                _tpl("T-a", "ALPHA", 2, {"name": "note", "type": "string"}),
                _tpl("T-b", "BETA", 2, _ref("alpha", "T-a", 1)),
                _tpl("T-b", "BETA", 3, _ref("alpha", "T-a", 2))],
        archive=[_tpl("A-a", "ALPHA", 1),
                 _tpl("A-b", "BETA", 1, _ref("alpha", "A-a", 1))],
    )

    assert _mapped(plan, "A-a") == {1: 1}
    assert _mapped(plan, "A-b") == {1: 2}


@pytest.mark.asyncio
async def test_a_cycle_through_a_compatible_version_is_mapped(db):
    # The target's ALPHA v3 has an optional field the archive's ALPHA v1
    # lacks: compatible, not the same schema, and still in the cycle.
    plan = await _plan(
        db,
        target=[_tpl("T-a", "ALPHA", 3, _ref("beta", "T-b", 5),
                     {"name": "note", "type": "string"}),
                _tpl("T-b", "BETA", 5, _ref("alpha", "T-a", 3))],
        archive=[_tpl("A-a", "ALPHA", 1, _ref("beta", "A-b", 1)),
                 _tpl("A-b", "BETA", 1, _ref("alpha", "A-a", 1))],
    )

    assert _mapped(plan, "A-a") == {1: 3}
    assert _mapped(plan, "A-b") == {1: 5}
    assert plan.alignment["A-a"].versions[1].reason == "compatible with target v3"


@pytest.mark.asyncio
async def test_a_version_whose_pinned_version_maps_nowhere_stays_unplaceable(db):
    # The target's BETA v2 pins ALPHA v1. The archive's ALPHA v1 differs from
    # every target ALPHA version and maps nowhere, so BETA v1 has no target
    # version that can take it: the archive's number 1 lines up with the
    # target's only by coincidence.
    plan = await _plan(
        db,
        target=[_tpl("T-a", "ALPHA", 1, AGE_STR),
                _tpl("T-b", "BETA", 2, _ref("alpha", "T-a", 1))],
        archive=[_tpl("A-a", "ALPHA", 1, AGE_INT),
                 _tpl("A-b", "BETA", 1, _ref("alpha", "A-a", 1))],
    )

    assert _mapped(plan, "A-a") == {1: None}
    assert _mapped(plan, "A-b") == {1: None}
    assert "references do not line up" in plan.alignment["A-b"].versions[1].reason


@pytest.mark.asyncio
async def test_a_pin_that_maps_to_an_earlier_version_is_not_read_as_the_later_one(db):
    # The archive pins BETA v1, which maps onto the target's BETA v2, which
    # pins ALPHA v1. The archive's ALPHA v2, which the target's v1 does not
    # match, is carried as history and maps nowhere: comparing it against
    # ALPHA v1 is the only way to tell the two apart.
    plan = await _plan(
        db,
        target=[_tpl("T-a", "ALPHA", 1, AGE_STR),
                _tpl("T-a", "ALPHA", 2, AGE_STR, NOTE),
                _tpl("T-b", "BETA", 2, _ref("alpha", "T-a", 1))],
        archive=[_tpl("A-a", "ALPHA", 1, AGE_STR),
                 _tpl("A-a", "ALPHA", 2, AGE_INT),
                 _tpl("A-b", "BETA", 1, _ref("alpha", "A-a", 2))],
    )

    assert _mapped(plan, "A-a") == {1: 1, 2: None}
    assert _mapped(plan, "A-b") == {1: None}


@pytest.mark.asyncio
async def test_a_mapping_that_needs_a_non_preferred_choice_is_found(db):
    # ALPHA v2 pins BETA v4 and ALPHA v1 pins BETA v5; both BETA versions pin
    # ALPHA v1. Taking ALPHA's preferred (highest) version v2 leaves no BETA
    # version whose pin agrees, so it and BETA v1 have no consistent mapping;
    # v1 together with BETA v5 does have one.
    plan = await _plan(
        db,
        target=[_tpl("T-a", "ALPHA", 1, _ref("beta", "T-b", 5)),
                _tpl("T-a", "ALPHA", 2, _ref("beta", "T-b", 4)),
                _tpl("T-b", "BETA", 4, _ref("alpha", "T-a", 1)),
                _tpl("T-b", "BETA", 5, _ref("alpha", "T-a", 1))],
        archive=[_tpl("A-a", "ALPHA", 1, _ref("beta", "A-b", 1)),
                 _tpl("A-b", "BETA", 1, _ref("alpha", "A-a", 1))],
    )

    assert _mapped(plan, "A-a") == {1: 1}
    assert _mapped(plan, "A-b") == {1: 5}


@pytest.mark.asyncio
async def test_a_later_template_in_the_archive_sees_the_earlier_ones_mapping(db):
    # BETA is listed first in the archive, so its version is compared before
    # ALPHA's mapping exists: the numbering coincidence must not map it either.
    plan = await _plan(
        db,
        target=[_tpl("T-a", "ALPHA", 1, AGE_STR),
                _tpl("T-b", "BETA", 2, _ref("alpha", "T-a", 1))],
        archive=[_tpl("A-b", "BETA", 1, _ref("alpha", "A-a", 1)),
                 _tpl("A-a", "ALPHA", 1, AGE_INT)],
    )

    assert _mapped(plan, "A-a") == {1: None}
    assert _mapped(plan, "A-b") == {1: None}


@pytest.mark.asyncio
async def test_an_unplaceable_template_does_not_stop_the_search_for_the_others(db):
    # The shape above, plus GAMMA, whose archived version matches no target
    # version. GAMMA has nothing to map onto from the start; that settles
    # GAMMA alone and must not keep ALPHA and BETA from the mapping they have.
    plan = await _plan(
        db,
        target=[_tpl("T-a", "ALPHA", 1, _ref("beta", "T-b", 5)),
                _tpl("T-a", "ALPHA", 2, _ref("beta", "T-b", 4)),
                _tpl("T-b", "BETA", 4, _ref("alpha", "T-a", 1)),
                _tpl("T-b", "BETA", 5, _ref("alpha", "T-a", 1)),
                _tpl("T-g", "GAMMA", 1, AGE_STR)],
        archive=[_tpl("A-a", "ALPHA", 1, _ref("beta", "A-b", 1)),
                 _tpl("A-b", "BETA", 1, _ref("alpha", "A-a", 1)),
                 _tpl("A-g", "GAMMA", 1, AGE_INT)],
    )

    assert _mapped(plan, "A-g") == {1: None}
    assert _mapped(plan, "A-a") == {1: 1}
    assert _mapped(plan, "A-b") == {1: 5}


@pytest.mark.asyncio
async def test_an_aligned_draft_against_an_active_version_refuses_the_pair(db):
    """align_templates mapped an archive draft onto an identical active target
    version and discarded the status difference, so the draft was absorbed
    without a word: the rule every other template pair follows (a draft is a
    real difference) never ran on this path (CASE-1275, f331b9ca review R1).

    The planner here takes every archived version as used; an archive draft
    that documents use is refused, mapped or not (CASE-1409). One nothing uses
    is history and never judged (test_case_1409_alignment)."""
    plan = await _plan(
        db,
        target=[_tpl("T-d", "DOC", 1, NOTE)],
        archive=[_tpl("A-d", "DOC", 1, NOTE, status="draft")],
    )

    assert not plan.compatible
    (issue,) = plan.incompatibilities
    assert issue.name == "DOC v1" and "draft" in issue.reason
    assert plan.alignment["A-d"].versions[1].target_version is None
    assert plan.aligned == 0


@pytest.mark.asyncio
async def test_an_aligned_retired_version_keeps_the_targets_status_and_says_so(db):
    plan = await _plan(
        db,
        target=[_tpl("T-d", "DOC", 1, NOTE)],
        archive=[_tpl("A-d", "DOC", 1, NOTE, status="inactive")],
    )

    assert plan.compatible, plan.incompatibilities
    assert plan.aligned == 1
    assert any("status" in str(w) for w in plan.target_wins), plan.target_wins
