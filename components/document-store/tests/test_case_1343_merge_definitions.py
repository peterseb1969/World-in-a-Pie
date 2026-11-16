"""Merge definition checks: the unique_names refusal names the terminology
the terms land in, a terminology's settings are read in the namespace merged
into first, and a term the archive retired adds no names to the target's.

- A terminology the merge adds is judged by the archive's unique_names; the
  refusal says so, not "the target's".
- The settings are read in the namespace merged into, by the index that
  leads with namespace. A terminology_id is unique per namespace only, so
  another namespace's terminology of the same id is not this one; only ids
  missing there (an externally mapped terminology) are read by id alone.
- Under extend_terminologies a term the archive retired gives the target's
  term none of its names, as a retired target term gains none.
"""

from __future__ import annotations

import pytest
from wip_archive.remap import IDRemapper

from document_store.services.backup_engine import COLLECTION_MAP
from tests.test_case_1293_merge_comparisons import (  # noqa: F401
    NAMESPACE,
    _planner,
    _seed,
    _term,
    _terminology,
    db,
)

ELSEWHERE = "c1343-elsewhere"


async def _clear_elsewhere(db) -> None:  # noqa: F811
    for db_name, coll_name in COLLECTION_MAP.values():
        await db[db_name][coll_name].delete_many({"namespace": ELSEWHERE})


@pytest.mark.asyncio
async def test_the_refusal_names_the_terminology_the_terms_land_in(db):  # noqa: F811
    plan = await _planner(db, add_missing=True, extend_terminologies=True).plan(
        {
            "terminologies": [_terminology("A-lov", unique_names=True)],
            "terms": [
                _term("A-m", "A-lov", "M", [{"value": "male"}]),
                _term("A-x", "A-lov", "X", [{"value": "MALE"}]),
            ],
        },
        NAMESPACE,
    )

    [issue] = plan.incompatibilities
    assert issue.reason.startswith(
        "the terminology these terms land in (APPS) has unique_names"
    ), issue.reason
    assert "the target's" not in issue.reason


@pytest.mark.asyncio
async def test_settings_are_read_in_the_namespace_merged_into(db):  # noqa: F811
    # The target's APPS has no unique_names; another namespace holds a
    # terminology of the same id that has it, stored after it.
    await _seed(db, "terminologies", [_terminology("T-lov")])
    await _seed(db, "terms", [_term("T-m", "T-lov", "M", [{"value": "male"}])])
    db_name, coll_name = COLLECTION_MAP["terminologies"]
    await db[db_name][coll_name].insert_one(
        {**_terminology("T-lov", unique_names=True), "namespace": ELSEWHERE},
    )
    t_db, t_coll = COLLECTION_MAP["terms"]
    await db[t_db][t_coll].insert_one(
        {**_term("O-m", "T-lov", "M", [{"value": "male"}]), "namespace": ELSEWHERE},
    )
    external = IDRemapper()
    external.terminology_map["A-lov"] = "T-lov"
    try:
        plan = await _planner(db, extend_terminologies=True).plan(
            {"terms": [_term("A-x", "A-lov", "X", [{"value": "male"}])]},
            NAMESPACE,
            external=external,
        )
    finally:
        await _clear_elsewhere(db)

    assert plan.compatible, plan.incompatibilities


@pytest.mark.asyncio
async def test_a_terminology_in_another_namespace_is_still_judged(db):  # noqa: F811
    # An externally mapped terminology outside the namespace merged into:
    # read by id alone, and its own terms are the ones a name clashes with.
    db_name, coll_name = COLLECTION_MAP["terminologies"]
    await db[db_name][coll_name].insert_one(
        {**_terminology("X-lov", unique_names=True), "namespace": ELSEWHERE},
    )
    t_db, t_coll = COLLECTION_MAP["terms"]
    await db[t_db][t_coll].insert_one(
        {**_term("X-m", "X-lov", "M", [{"value": "male"}]), "namespace": ELSEWHERE},
    )
    external = IDRemapper()
    external.terminology_map["A-lov"] = "X-lov"
    try:
        plan = await _planner(db, extend_terminologies=True).plan(
            {"terms": [_term("A-x", "A-lov", "X", [{"value": "male"}])]},
            NAMESPACE,
            external=external,
        )
    finally:
        await _clear_elsewhere(db)

    assert not plan.compatible
    [issue] = plan.incompatibilities
    assert issue.detail["unique_names"]["name_collisions"] == ["male"]


@pytest.mark.asyncio
async def test_a_term_the_archive_retired_adds_no_names(db):  # noqa: F811
    await _seed(db, "terminologies", [_terminology("T-lov")])
    await _seed(db, "terms", [_term("T-term", "T-lov", "Masque", [{"value": "wip-aa"}])])
    plan = await _planner(db, extend_terminologies=True).plan(
        {
            "terminologies": [_terminology("A-lov")],
            "terms": [_term(
                "A-term", "A-lov", "Masque", [{"value": "mask"}, {"value": "wip-aa"}],
                status="deprecated",
            )],
        },
        NAMESPACE,
    )

    assert plan.compatible, plan.incompatibilities
    assert plan.extend_names == []
    assert any("names" in w.fields for w in plan.target_wins), plan.target_wins
