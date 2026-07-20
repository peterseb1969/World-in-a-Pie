"""Definitions compatibility — pass 1 of a merge.

Runs against the real test MongoDB: the pass answers "does the target already
have this definition, by content?", and a query that matches nothing in
practice is exactly the failure worth catching.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.services.backup_engine import COLLECTION_MAP
from document_store.services.merge_definitions import (
    DEFINITION_TYPES,
    DefinitionsPlanner,
    compare_definitions,
)

NAMESPACE = "merge-defs-ns"


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(
        os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
    )

    async def _clear():
        for db_name, coll_name in COLLECTION_MAP.values():
            await client[db_name][coll_name].delete_many({"namespace": NAMESPACE})

    await _clear()
    yield client
    await _clear()
    client.close()


async def _seed(db, entity_type, rows):
    db_name, coll_name = COLLECTION_MAP[entity_type]
    await db[db_name][coll_name].insert_many([dict(r) for r in rows])


def _planner(db, **flags):
    return DefinitionsPlanner(db, COLLECTION_MAP, **flags)


def _terminology(tid, value="GENDER", **extra):
    return {"terminology_id": tid, "namespace": NAMESPACE, "value": value, **extra}


def _term(tid, terminology_id, value="M", **extra):
    return {
        "term_id": tid, "namespace": NAMESPACE,
        "terminology_id": terminology_id, "value": value, **extra,
    }


def _template(tid, value="PATIENT", version=1, **extra):
    return {
        "template_id": tid, "namespace": NAMESPACE, "value": value,
        "version": version, "fields": [{"name": "age", "type": "integer"}],
        **extra,
    }


# ---------------------------------------------------------------------------
# Field classification
# ---------------------------------------------------------------------------


class TestCompareDefinitions:
    def test_identical_definitions_have_no_differences(self):
        row = _terminology("X", label="Gender")
        assert compare_definitions(dict(row), dict(row)) == ([], [])

    def test_audit_fields_are_not_differences(self):
        archive = _terminology("X", updated_at="2026-01-01")
        target = _terminology("X", updated_at="2026-07-01")
        assert compare_definitions(archive, target) == ([], [])

    def test_canonical_ids_are_not_differences(self):
        # Two installs holding the same definition under different UUIDs is
        # the normal case this pass exists to recognise, not a finding.
        assert compare_definitions(
            _terminology("A-uuid"), _terminology("B-uuid")
        ) == ([], [])

    def test_label_is_cosmetic(self):
        cosmetic, substantive = compare_definitions(
            _terminology("A", label="Sex"), _terminology("B", label="Gender")
        )
        assert cosmetic == ["label"] and substantive == []

    def test_schema_is_substantive(self):
        cosmetic, substantive = compare_definitions(
            _template("A", fields=[{"name": "age", "type": "string"}]),
            _template("B"),
        )
        assert substantive == ["fields"] and cosmetic == []


# ---------------------------------------------------------------------------
# Verify-only default
# ---------------------------------------------------------------------------


class TestVerifyOnlyDefault:
    @pytest.mark.asyncio
    async def test_identical_definitions_are_compatible(self, db):
        await _seed(db, "terminologies", [_terminology("B-lov")])
        plan = await _planner(db).plan(
            {"terminologies": [_terminology("A-lov")]}, NAMESPACE
        )

        assert plan.compatible
        assert plan.unchanged["terminologies"] == 1
        assert plan.to_add["terminologies"] == []

    @pytest.mark.asyncio
    async def test_a_definition_the_target_lacks_refuses_by_default(self, db):
        # Changing a live namespace's definitions is an active decision, never
        # a side effect of restoring data into it.
        plan = await _planner(db).plan(
            {"terminologies": [_terminology("A-lov")]}, NAMESPACE
        )

        assert not plan.compatible
        (issue,) = plan.incompatibilities
        assert "does not have it" in issue.reason
        assert "add_missing=true" in issue.reason

    @pytest.mark.asyncio
    async def test_same_name_different_schema_is_incompatible(self, db):
        # The case that matters: merging documents under it would validate one
        # side's data against the other's contract.
        await _seed(db, "templates", [_template("B-tpl")])
        plan = await _planner(db).plan(
            {"templates": [
                _template("A-tpl", fields=[{"name": "age", "type": "string"}]),
            ]},
            NAMESPACE,
        )

        assert not plan.compatible
        (issue,) = plan.incompatibilities
        assert issue.name == "PATIENT v1"
        assert "different content" in issue.reason
        assert issue.detail["fields"]["target"] != issue.detail["fields"]["archive"]

    @pytest.mark.asyncio
    async def test_nothing_is_written_by_the_pass(self, db):
        # It reads. Even with the flags on, applying is the caller's job.
        await _seed(db, "terminologies", [_terminology("B-lov")])
        await _planner(db, add_missing=True).plan(
            {"terminologies": [_terminology("A-lov", value="COUNTRY")]},
            NAMESPACE,
        )

        db_name, coll_name = COLLECTION_MAP["terminologies"]
        rows = await db[db_name][coll_name].find(
            {"namespace": NAMESPACE}
        ).to_list(length=None)
        assert [r["value"] for r in rows] == ["GENDER"]


# ---------------------------------------------------------------------------
# The mapping table
# ---------------------------------------------------------------------------


class TestMappingTable:
    @pytest.mark.asyncio
    async def test_matching_by_content_yields_the_id_mapping(self, db):
        # Establishing that the target's GENDER is the archive's GENDER IS the
        # act of learning A-lov -> B-lov.
        await _seed(db, "terminologies", [_terminology("B-lov")])
        plan = await _planner(db).plan(
            {"terminologies": [_terminology("A-lov")]}, NAMESPACE
        )

        assert plan.mapping["terminologies"] == {"A-lov": "B-lov"}

    @pytest.mark.asyncio
    async def test_same_install_yields_no_mapping_entries(self, db):
        # Identical IDs need no remapping — the same pass covers both cases
        # without the caller declaring which one they are in.
        await _seed(db, "terminologies", [_terminology("SAME")])
        plan = await _planner(db).plan(
            {"terminologies": [_terminology("SAME")]}, NAMESPACE
        )

        assert plan.mapping["terminologies"] == {}
        assert plan.compatible

    @pytest.mark.asyncio
    async def test_a_term_is_matched_through_its_remapped_terminology(self, db):
        # A term's natural key contains its terminology, so it can only be
        # recognised after the terminology pass has mapped it.
        await _seed(db, "terminologies", [_terminology("B-lov")])
        await _seed(db, "terms", [_term("B-term", "B-lov")])

        plan = await _planner(db).plan(
            {
                "terminologies": [_terminology("A-lov")],
                "terms": [_term("A-term", "A-lov")],
            },
            NAMESPACE,
        )

        assert plan.compatible
        assert plan.mapping["terms"] == {"A-term": "B-term"}

    @pytest.mark.asyncio
    async def test_templates_map_on_value_and_version(self, db):
        await _seed(db, "templates", [_template("B-tpl")])
        plan = await _planner(db).plan(
            {"templates": [_template("A-tpl")]}, NAMESPACE
        )

        assert plan.mapping["templates"] == {"A-tpl": "B-tpl"}


# ---------------------------------------------------------------------------
# Opt-in strategies
# ---------------------------------------------------------------------------


class TestOptInStrategies:
    @pytest.mark.asyncio
    async def test_add_missing_makes_an_absent_template_addable(self, db):
        plan = await _planner(db, add_missing=True).plan(
            {"templates": [_template("A-tpl")]}, NAMESPACE
        )

        assert plan.compatible
        assert [t["template_id"] for t in plan.to_add["templates"]] == ["A-tpl"]

    @pytest.mark.asyncio
    async def test_extend_terminologies_covers_terms_not_templates(self, db):
        # The two strategies are separate decisions: allowing new vocabulary
        # entries is not the same as allowing new schemas.
        plan = await _planner(db, extend_terminologies=True).plan(
            {
                "terms": [_term("A-term", "A-lov")],
                "templates": [_template("A-tpl")],
            },
            NAMESPACE,
        )

        assert [t["term_id"] for t in plan.to_add["terms"]] == ["A-term"]
        assert plan.to_add["templates"] == []
        (issue,) = plan.incompatibilities
        assert issue.entity_type == "templates"

    @pytest.mark.asyncio
    async def test_extra_terms_on_the_target_are_harmless(self, db):
        # Nothing in the archive references them, so there is nothing to do.
        await _seed(db, "terminologies", [_terminology("B-lov")])
        await _seed(db, "terms", [
            _term("B-m", "B-lov", value="M"),
            _term("B-extra", "B-lov", value="NONBINARY"),
        ])

        plan = await _planner(db).plan(
            {
                "terminologies": [_terminology("A-lov")],
                "terms": [_term("A-m", "A-lov", value="M")],
            },
            NAMESPACE,
        )

        assert plan.compatible
        assert plan.to_add["terms"] == []


# ---------------------------------------------------------------------------
# Cosmetic differences
# ---------------------------------------------------------------------------


class TestTargetWins:
    @pytest.mark.asyncio
    async def test_a_differing_label_is_not_fatal_but_is_reported(self, db):
        # Silently rewriting or discarding vocabulary metadata is what an
        # operator finds out about later, from a UI that changed under them.
        await _seed(db, "terminologies", [_terminology("B-lov", label="Gender")])
        plan = await _planner(db).plan(
            {"terminologies": [_terminology("A-lov", label="Sex")]}, NAMESPACE
        )

        assert plan.compatible
        (note,) = plan.target_wins
        assert note.fields == ["label"]
        assert note.detail["label"] == {"kept": "Gender", "discarded": "Sex"}

    @pytest.mark.asyncio
    async def test_differing_aliases_on_a_term_are_reported(self, db):
        await _seed(db, "terminologies", [_terminology("B-lov")])
        await _seed(db, "terms", [_term("B-m", "B-lov", aliases=["Male"])])

        plan = await _planner(db).plan(
            {
                "terminologies": [_terminology("A-lov")],
                "terms": [_term("A-m", "A-lov", aliases=["Male", "MR"])],
            },
            NAMESPACE,
        )

        assert plan.compatible
        (note,) = plan.target_wins
        assert note.entity_type == "terms" and note.fields == ["aliases"]

    @pytest.mark.asyncio
    async def test_summary_counts_every_type(self, db):
        plan = await _planner(db, add_missing=True).plan(
            {"terminologies": [_terminology("A-lov")]}, NAMESPACE
        )
        summary = plan.summary()

        assert set(summary) == set(DEFINITION_TYPES)
        assert summary["terminologies"] == {"unchanged": 0, "add": 1, "mapped": 0}
