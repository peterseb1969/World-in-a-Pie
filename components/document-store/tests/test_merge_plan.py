"""Tests for merge-restore planning (restore modes, Phase 1).

The planner decides what a merge would do before it writes anything, so its
classification IS the contract: an entity is inserted, clashes with something
the target already holds, or conflicts because the two sides disagree about
identity. Classification runs against the real test MongoDB — a query shape
that matches nothing in practice is exactly the bug worth catching, and a
stand-in collection would answer it happily.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.services.backup_engine import COLLECTION_MAP
from document_store.services.merge_plan import (
    KEY_BATCH_SIZE,
    MERGE_ENTITY_SPECS,
    MergePlanner,
    diff_entities,
    key_of,
)

# This suite's own namespace, so it never sees another suite's rows.
NAMESPACE = "merge-plan-ns"


@pytest_asyncio.fixture
async def planner():
    """A planner over the real databases, cleared before and after."""
    client = AsyncIOMotorClient(
        os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
    )

    async def _clear():
        for db_name, coll_name in COLLECTION_MAP.values():
            await client[db_name][coll_name].delete_many({"namespace": NAMESPACE})

    await _clear()
    instance = MergePlanner(client, COLLECTION_MAP)
    instance.seed = _seeder(client)  # type: ignore[attr-defined]
    yield instance
    await _clear()
    client.close()


def _seeder(client):
    async def seed(entity_type, rows):
        if not rows:
            return
        db_name, coll_name = COLLECTION_MAP[entity_type]
        await client[db_name][coll_name].insert_many([dict(r) for r in rows])

    return seed


# ---------------------------------------------------------------------------
# Key extraction and diffing
# ---------------------------------------------------------------------------


class TestKeyOf:
    def test_returns_tuple_of_field_values(self):
        assert key_of({"a": 1, "b": "x"}, ("a", "b")) == (1, "x")

    def test_missing_or_empty_component_means_no_key(self):
        # A blank component is "this entity has no key of this kind" — an
        # identity-less document, a legacy entry with an empty composite key.
        # Keying on the blank would collide every such entity with every other.
        assert key_of({"a": 1}, ("a", "b")) is None
        assert key_of({"a": 1, "b": ""}, ("a", "b")) is None

    def test_no_fields_means_no_key(self):
        assert key_of({"a": 1}, ()) is None


class TestDiffEntities:
    def test_identical_entities_have_no_diff(self):
        assert diff_entities({"value": "GENDER"}, {"value": "GENDER"}) == {}

    def test_audit_fields_are_not_schema_differences(self):
        # Two faithful copies of one terminology differ in updated_at; calling
        # that a schema clash would make every merge fail under on_schema_clash=fail.
        archive = {"value": "GENDER", "updated_at": "2026-01-01", "_id": "a"}
        target = {"value": "GENDER", "updated_at": "2026-07-01", "_id": "b"}
        assert diff_entities(archive, target) == {}

    def test_reports_both_sides_of_a_difference(self):
        diff = diff_entities({"label": "New"}, {"label": "Old"})
        assert diff == {"label": {"archive": "New", "target": "Old"}}

    def test_field_present_on_only_one_side_is_a_difference(self):
        diff = diff_entities({"label": "New"}, {})
        assert diff["label"] == {"archive": "New", "target": None}


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class TestPlanClassification:
    @pytest.mark.asyncio
    async def test_entity_absent_from_target_is_inserted(self, planner):
        plan = await planner.plan(
            "terminologies",
            [{"terminology_id": "T1", "namespace": NAMESPACE, "value": "GENDER"}],
            NAMESPACE,
        )
        assert [e["terminology_id"] for e in plan.to_insert] == ["T1"]
        assert plan.clashes == [] and plan.conflicts == []

    @pytest.mark.asyncio
    async def test_identical_entity_is_an_unchanged_clash(self, planner):
        row = {"terminology_id": "T1", "namespace": NAMESPACE, "value": "GENDER"}
        await planner.seed("terminologies", [row])

        plan = await planner.plan("terminologies", [dict(row)], NAMESPACE)

        assert plan.to_insert == []
        assert len(plan.identical_clashes) == 1
        assert plan.summary() == {
            "insert": 0, "unchanged": 1, "clash": 0, "conflict": 0,
        }

    @pytest.mark.asyncio
    async def test_differing_entity_is_a_clash_carrying_the_diff(self, planner):
        await planner.seed("terminologies", [
            {"terminology_id": "T1", "namespace": NAMESPACE, "value": "GENDER",
             "label": "Gender"},
        ])

        plan = await planner.plan(
            "terminologies",
            [{"terminology_id": "T1", "namespace": NAMESPACE, "value": "GENDER",
              "label": "Sex"}],
            NAMESPACE,
        )

        (clash,) = plan.differing_clashes
        assert clash.diff == {"label": {"archive": "Sex", "target": "Gender"}}

    @pytest.mark.asyncio
    async def test_other_namespace_is_not_a_clash(self, planner):
        # The same value in another namespace is a different entity.
        await planner.seed("terminologies", [
            {"terminology_id": "T1", "namespace": "someone-else", "value": "GENDER"},
        ])

        plan = await planner.plan(
            "terminologies",
            [{"terminology_id": "T1", "namespace": NAMESPACE, "value": "GENDER"}],
            NAMESPACE,
        )

        assert len(plan.to_insert) == 1

    @pytest.mark.asyncio
    async def test_compound_logical_key_matches_on_all_parts(self, planner):
        await planner.seed("terms", [
            {"term_id": "X1", "namespace": NAMESPACE, "terminology_id": "T1",
             "value": "M"},
        ])

        plan = await planner.plan(
            "terms",
            [
                {"term_id": "X1", "namespace": NAMESPACE, "terminology_id": "T1",
                 "value": "M"},
                # Same value under a different terminology — a different term.
                {"term_id": "X2", "namespace": NAMESPACE, "terminology_id": "T2",
                 "value": "M"},
            ],
            NAMESPACE,
        )

        assert len(plan.clashes) == 1
        assert [e["term_id"] for e in plan.to_insert] == ["X2"]


class TestIdentityConflicts:
    """The archive and the target disagree about which thing is which.

    Merge v1 preserves IDs, so a half-match is never resolved silently: it
    means the ID was reused for something else, or one real-world entity now
    has two IDs. Both need re-minting, which is the cross-install mode.
    """

    @pytest.mark.asyncio
    async def test_same_id_under_a_different_logical_key_conflicts(self, planner):
        await planner.seed("terminologies", [
            {"terminology_id": "T1", "namespace": NAMESPACE, "value": "COUNTRY"},
        ])

        plan = await planner.plan(
            "terminologies",
            [{"terminology_id": "T1", "namespace": NAMESPACE, "value": "GENDER"}],
            NAMESPACE,
        )

        assert plan.to_insert == [] and plan.clashes == []
        (conflict,) = plan.conflicts
        assert "different entity" in conflict.reason
        assert "COUNTRY" in conflict.reason and "GENDER" in conflict.reason

    @pytest.mark.asyncio
    async def test_same_logical_key_under_a_different_id_conflicts(self, planner):
        await planner.seed("terminologies", [
            {"terminology_id": "T-OTHER", "namespace": NAMESPACE, "value": "GENDER"},
        ])

        plan = await planner.plan(
            "terminologies",
            [{"terminology_id": "T1", "namespace": NAMESPACE, "value": "GENDER"}],
            NAMESPACE,
        )

        (conflict,) = plan.conflicts
        assert "different ID" in conflict.reason
        assert "T-OTHER" in conflict.reason

    @pytest.mark.asyncio
    async def test_keys_matching_two_different_rows_conflicts(self, planner):
        await planner.seed("terms", [
            {"term_id": "X1", "namespace": NAMESPACE, "terminology_id": "T1",
             "value": "F"},
            {"term_id": "X2", "namespace": NAMESPACE, "terminology_id": "T1",
             "value": "M"},
        ])

        plan = await planner.plan(
            "terms",
            [{"term_id": "X1", "namespace": NAMESPACE, "terminology_id": "T1",
              "value": "M"}],
            NAMESPACE,
        )

        (conflict,) = plan.conflicts
        assert "two different rows" in conflict.reason

    @pytest.mark.asyncio
    async def test_entity_without_a_logical_key_matches_on_id_alone(self, planner):
        # A registry entry with an empty composite-key hash opts out of dedup;
        # there is no logical key to disagree with, so ID matching stands.
        entry = {"entry_id": "E1", "namespace": NAMESPACE,
                 "entity_type": "templates", "primary_composite_key_hash": ""}
        await planner.seed("registry_entries", [entry])

        plan = await planner.plan("registry_entries", [dict(entry)], NAMESPACE)

        assert plan.conflicts == []
        assert len(plan.clashes) == 1

    @pytest.mark.asyncio
    async def test_entity_without_an_id_matches_on_the_logical_key_alone(
        self, planner
    ):
        # A term relation IS its endpoints — it carries no ID of its own.
        assert MERGE_ENTITY_SPECS["term_relations"].id_fields == ()
        relation = {
            "namespace": NAMESPACE, "source_term_id": "A", "target_term_id": "B",
            "relation_type": "is_a",
        }
        await planner.seed("term_relations", [relation])

        plan = await planner.plan("term_relations", [dict(relation)], NAMESPACE)

        assert plan.conflicts == []
        assert len(plan.clashes) == 1


class TestDocumentPlanning:
    @pytest.mark.asyncio
    async def test_clash_carries_every_target_version(self, planner):
        # Overwrite appends on top of the target's head, so the caller needs
        # all the target's versions, not an arbitrary one.
        await planner.seed("documents", [
            {"document_id": "D1", "namespace": NAMESPACE, "template_id": "TPL",
             "identity_hash": "h1", "version": v}
            for v in (1, 2)
        ])

        plan = await planner.plan(
            "documents",
            [{"document_id": "D1", "namespace": NAMESPACE, "template_id": "TPL",
              "identity_hash": "h1", "version": 1, "data": {"x": 1}}],
            NAMESPACE,
        )

        (clash,) = plan.clashes
        assert sorted(t["version"] for t in clash.targets) == [1, 2]

    @pytest.mark.asyncio
    async def test_projected_reads_do_not_produce_phantom_diffs(self, planner):
        # Documents are read with a projection, so a field-level diff would
        # report every omitted field as a difference. Document clashes are
        # resolved by the on_clash policy, not by diffing — and a clash is
        # never reported as "unchanged" on the strength of a partial read.
        await planner.seed("documents", [
            {"document_id": "D1", "namespace": NAMESPACE, "template_id": "TPL",
             "identity_hash": "h1", "version": 1, "data": {"x": 1}},
        ])

        plan = await planner.plan(
            "documents",
            [{"document_id": "D1", "namespace": NAMESPACE, "template_id": "TPL",
              "identity_hash": "h1", "version": 1, "data": {"x": 999}}],
            NAMESPACE,
        )

        (clash,) = plan.clashes
        assert clash.diff == {}
        assert "data" not in clash.target
        assert plan.summary()["clash"] == 1
        assert plan.summary()["unchanged"] == 0

    @pytest.mark.asyncio
    async def test_identity_less_documents_match_by_document_id_only(self, planner):
        # Append-only templates have no logical identity — same document_id
        # means the same physical row; anything else is new.
        await planner.seed("documents", [
            {"document_id": "D1", "namespace": NAMESPACE, "template_id": "TPL",
             "identity_hash": "", "version": 1},
        ])

        plan = await planner.plan(
            "documents",
            [
                {"document_id": "D1", "namespace": NAMESPACE, "template_id": "TPL",
                 "identity_hash": "", "version": 1},
                {"document_id": "D2", "namespace": NAMESPACE, "template_id": "TPL",
                 "identity_hash": "", "version": 1},
            ],
            NAMESPACE,
        )

        assert plan.conflicts == []
        assert len(plan.clashes) == 1
        assert [e["document_id"] for e in plan.to_insert] == ["D2"]


class TestBatching:
    @pytest.mark.asyncio
    async def test_more_keys_than_one_batch_still_classifies_every_entity(
        self, planner
    ):
        # Probes are chunked; a key landing in the second chunk must be
        # classified against the target just as one in the first.
        total = KEY_BATCH_SIZE + 10
        await planner.seed("terminologies", [
            {"terminology_id": f"T{i}", "namespace": NAMESPACE, "value": f"V{i}"}
            for i in range(total)
        ])

        plan = await planner.plan(
            "terminologies",
            [
                {"terminology_id": f"T{i}", "namespace": NAMESPACE,
                 "value": f"V{i}"}
                for i in range(total)
            ],
            NAMESPACE,
        )

        assert len(plan.identical_clashes) == total
        assert plan.to_insert == []

    @pytest.mark.asyncio
    async def test_no_entities_means_an_empty_plan(self, planner):
        plan = await planner.plan("terminologies", [], NAMESPACE)
        assert plan.summary() == {
            "insert": 0, "unchanged": 0, "clash": 0, "conflict": 0,
        }


# ---------------------------------------------------------------------------
# Cross-install matching
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def cross_planner():
    """A planner in cross-install mode over the real databases."""
    client = AsyncIOMotorClient(
        os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
    )

    async def _clear():
        for db_name, coll_name in COLLECTION_MAP.values():
            await client[db_name][coll_name].delete_many({"namespace": NAMESPACE})

    await _clear()
    instance = MergePlanner(client, COLLECTION_MAP, cross_install=True)
    instance.seed = _seeder(client)  # type: ignore[attr-defined]
    yield instance
    await _clear()
    client.close()


class TestCrossInstallMatching:
    """Two installs that never shared an ID space.

    The evidence that means "identity is corrupted" on one install means
    "both sides created the same real-world thing" across two. Nothing in the
    data distinguishes them — hence the caller's declaration.
    """

    @pytest.mark.asyncio
    async def test_same_logical_key_under_another_id_is_a_match(self, cross_planner):
        await cross_planner.seed("terminologies", [
            {"terminology_id": "B-uuid", "namespace": NAMESPACE, "value": "GENDER"},
        ])

        plan = await cross_planner.plan(
            "terminologies",
            [{"terminology_id": "A-uuid", "namespace": NAMESPACE, "value": "GENDER"}],
            NAMESPACE,
        )

        assert plan.conflicts == []
        assert plan.to_insert == []
        (match,) = plan.matches
        assert (match.old_id, match.new_id) == ("A-uuid", "B-uuid")

    @pytest.mark.asyncio
    async def test_the_same_case_is_a_conflict_for_a_same_install_merge(self, planner):
        # The discriminating test: identical data, opposite verdicts. If these
        # two ever agree, the mode flag has stopped doing anything.
        await planner.seed("terminologies", [
            {"terminology_id": "B-uuid", "namespace": NAMESPACE, "value": "GENDER"},
        ])

        plan = await planner.plan(
            "terminologies",
            [{"terminology_id": "A-uuid", "namespace": NAMESPACE, "value": "GENDER"}],
            NAMESPACE,
        )

        assert plan.matches == []
        assert len(plan.conflicts) == 1

    @pytest.mark.asyncio
    async def test_entity_the_target_lacks_is_still_an_insert(self, cross_planner):
        plan = await cross_planner.plan(
            "terminologies",
            [{"terminology_id": "A-uuid", "namespace": NAMESPACE, "value": "GENDER"}],
            NAMESPACE,
        )

        assert plan.matches == []
        assert [e["terminology_id"] for e in plan.to_insert] == ["A-uuid"]

    @pytest.mark.asyncio
    async def test_agreeing_ids_are_an_ordinary_clash_not_a_match(self, cross_planner):
        # UUIDs are unique enough that the two installs can genuinely share an
        # entity ID (a common ancestor archive). Nothing to remap, so it falls
        # through to the normal policy path.
        row = {"terminology_id": "SHARED", "namespace": NAMESPACE, "value": "GENDER"}
        await cross_planner.seed("terminologies", [row])

        plan = await cross_planner.plan("terminologies", [dict(row)], NAMESPACE)

        assert plan.matches == []
        assert len(plan.clashes) == 1

    @pytest.mark.asyncio
    async def test_matches_are_counted_in_the_summary(self, cross_planner):
        await cross_planner.seed("terminologies", [
            {"terminology_id": "B-uuid", "namespace": NAMESPACE, "value": "GENDER"},
        ])

        plan = await cross_planner.plan(
            "terminologies",
            [
                {"terminology_id": "A-uuid", "namespace": NAMESPACE,
                 "value": "GENDER"},
                {"terminology_id": "A-other", "namespace": NAMESPACE,
                 "value": "COUNTRY"},
            ],
            NAMESPACE,
        )

        assert plan.summary()["matched"] == 1
        assert plan.summary()["insert"] == 1

    @pytest.mark.asyncio
    async def test_template_match_maps_the_id_not_the_version(self, cross_planner):
        # Templates key on (template_id, version); a reference points at the
        # template, never at one of its versions.
        await cross_planner.seed("templates", [
            {"template_id": "B-tpl", "namespace": NAMESPACE, "value": "PATIENT",
             "version": 1},
        ])

        plan = await cross_planner.plan(
            "templates",
            [{"template_id": "A-tpl", "namespace": NAMESPACE, "value": "PATIENT",
              "version": 1}],
            NAMESPACE,
        )

        (match,) = plan.matches
        assert (match.old_id, match.new_id) == ("A-tpl", "B-tpl")
