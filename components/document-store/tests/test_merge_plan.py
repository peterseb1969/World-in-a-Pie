"""Tests for merge-restore planning (restore modes, Phase 1).

The planner decides what a merge would do before it writes anything, so its
classification IS the contract: an entity is inserted, clashes with something
the target already holds, or conflicts because the two sides disagree about
identity. These tests drive it through a fake collection that really evaluates
the queries the planner builds, so a wrong query shape fails here rather than
silently matching nothing against a mock.
"""

from __future__ import annotations

import pytest

from document_store.services.backup_engine import COLLECTION_MAP
from document_store.services.merge_plan import (
    KEY_BATCH_SIZE,
    MERGE_ENTITY_SPECS,
    MergePlanner,
    diff_entities,
    key_of,
)


class _FakeCollection:
    """In-memory collection that honours the query shapes the planner emits."""

    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]
        self.queries: list[dict] = []

    def find(self, query, projection=None):
        self.queries.append(query)
        matched = [r for r in self.rows if self._matches(r, query)]
        if projection:
            matched = [
                {k: v for k, v in r.items() if k in projection} for r in matched
            ]
        return _AsyncRows(matched)

    @staticmethod
    def _matches(row, query):
        for key, condition in query.items():
            if key == "$or":
                if not any(
                    all(row.get(k) == v for k, v in branch.items())
                    for branch in condition
                ):
                    return False
            elif isinstance(condition, dict) and "$in" in condition:
                if row.get(key) not in condition["$in"]:
                    return False
            elif row.get(key) != condition:
                return False
        return True


class _AsyncRows:
    def __init__(self, rows):
        self._rows = list(rows)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._rows:
            raise StopAsyncIteration
        return dict(self._rows.pop(0))


def _planner(entity_type, target_rows):
    """A planner whose only populated collection is ``entity_type``'s."""
    collection = _FakeCollection(target_rows)
    db_name, coll_name = COLLECTION_MAP[entity_type]

    class _DB:
        def __getitem__(self, name):
            return collection if name == coll_name else _FakeCollection([])

    class _Client:
        def __getitem__(self, name):
            return _DB()

    return MergePlanner(_Client(), COLLECTION_MAP), collection


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
    async def test_entity_absent_from_target_is_inserted(self):
        planner, _ = _planner("terminologies", [])
        plan = await planner.plan(
            "terminologies",
            [{"terminology_id": "T1", "namespace": "kb", "value": "GENDER"}],
            "kb",
        )
        assert [e["terminology_id"] for e in plan.to_insert] == ["T1"]
        assert plan.clashes == [] and plan.conflicts == []

    @pytest.mark.asyncio
    async def test_identical_entity_is_an_unchanged_clash(self):
        row = {"terminology_id": "T1", "namespace": "kb", "value": "GENDER"}
        planner, _ = _planner("terminologies", [row])
        plan = await planner.plan("terminologies", [dict(row)], "kb")

        assert plan.to_insert == []
        assert len(plan.identical_clashes) == 1
        assert plan.summary() == {
            "insert": 0, "unchanged": 1, "clash": 0, "conflict": 0,
        }

    @pytest.mark.asyncio
    async def test_differing_entity_is_a_clash_carrying_the_diff(self):
        planner, _ = _planner("terminologies", [
            {"terminology_id": "T1", "namespace": "kb", "value": "GENDER",
             "label": "Gender"},
        ])
        plan = await planner.plan(
            "terminologies",
            [{"terminology_id": "T1", "namespace": "kb", "value": "GENDER",
              "label": "Sex"}],
            "kb",
        )

        (clash,) = plan.differing_clashes
        assert clash.diff == {"label": {"archive": "Sex", "target": "Gender"}}

    @pytest.mark.asyncio
    async def test_other_namespace_is_not_a_clash(self):
        # The same value in another namespace is a different entity.
        planner, _ = _planner("terminologies", [
            {"terminology_id": "T1", "namespace": "other", "value": "GENDER"},
        ])
        plan = await planner.plan(
            "terminologies",
            [{"terminology_id": "T1", "namespace": "kb", "value": "GENDER"}],
            "kb",
        )
        assert len(plan.to_insert) == 1

    @pytest.mark.asyncio
    async def test_compound_logical_key_matches_on_all_parts(self):
        planner, _ = _planner("terms", [
            {"term_id": "X1", "namespace": "kb", "terminology_id": "T1",
             "value": "M"},
        ])
        plan = await planner.plan(
            "terms",
            [
                {"term_id": "X1", "namespace": "kb", "terminology_id": "T1",
                 "value": "M"},
                # Same value under a different terminology — a different term.
                {"term_id": "X2", "namespace": "kb", "terminology_id": "T2",
                 "value": "M"},
            ],
            "kb",
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
    async def test_same_id_under_a_different_logical_key_conflicts(self):
        planner, _ = _planner("terminologies", [
            {"terminology_id": "T1", "namespace": "kb", "value": "COUNTRY"},
        ])
        plan = await planner.plan(
            "terminologies",
            [{"terminology_id": "T1", "namespace": "kb", "value": "GENDER"}],
            "kb",
        )

        assert plan.to_insert == [] and plan.clashes == []
        (conflict,) = plan.conflicts
        assert "different entity" in conflict.reason
        assert "COUNTRY" in conflict.reason and "GENDER" in conflict.reason

    @pytest.mark.asyncio
    async def test_same_logical_key_under_a_different_id_conflicts(self):
        planner, _ = _planner("terminologies", [
            {"terminology_id": "T-OTHER", "namespace": "kb", "value": "GENDER"},
        ])
        plan = await planner.plan(
            "terminologies",
            [{"terminology_id": "T1", "namespace": "kb", "value": "GENDER"}],
            "kb",
        )

        (conflict,) = plan.conflicts
        assert "different ID" in conflict.reason
        assert "T-OTHER" in conflict.reason

    @pytest.mark.asyncio
    async def test_keys_matching_two_different_rows_conflicts(self):
        planner, _ = _planner("terms", [
            {"term_id": "X1", "namespace": "kb", "terminology_id": "T1",
             "value": "F"},
            {"term_id": "X2", "namespace": "kb", "terminology_id": "T1",
             "value": "M"},
        ])
        plan = await planner.plan(
            "terms",
            [{"term_id": "X1", "namespace": "kb", "terminology_id": "T1",
              "value": "M"}],
            "kb",
        )

        (conflict,) = plan.conflicts
        assert "two different rows" in conflict.reason

    @pytest.mark.asyncio
    async def test_entity_without_a_logical_key_matches_on_id_alone(self):
        # A registry entry with an empty composite-key hash opts out of dedup;
        # there is no logical key to disagree with, so ID matching stands.
        planner, _ = _planner("registry_entries", [
            {"entry_id": "E1", "namespace": "kb", "entity_type": "templates",
             "primary_composite_key_hash": ""},
        ])
        plan = await planner.plan(
            "registry_entries",
            [{"entry_id": "E1", "namespace": "kb", "entity_type": "templates",
              "primary_composite_key_hash": ""}],
            "kb",
        )

        assert plan.conflicts == []
        assert len(plan.clashes) == 1

    @pytest.mark.asyncio
    async def test_entity_without_an_id_matches_on_the_logical_key_alone(self):
        # A term relation IS its endpoints — it carries no ID of its own.
        assert MERGE_ENTITY_SPECS["term_relations"].id_fields == ()
        relation = {
            "namespace": "kb", "source_term_id": "A", "target_term_id": "B",
            "relation_type": "is_a",
        }
        planner, _ = _planner("term_relations", [relation])
        plan = await planner.plan("term_relations", [dict(relation)], "kb")

        assert plan.conflicts == []
        assert len(plan.clashes) == 1


class TestDocumentPlanning:
    @pytest.mark.asyncio
    async def test_clash_carries_every_target_version(self):
        # Overwrite appends on top of the target's head, so the caller needs
        # all the target's versions, not an arbitrary one.
        planner, _ = _planner("documents", [
            {"document_id": "D1", "namespace": "kb", "template_id": "TPL",
             "identity_hash": "h1", "version": 1},
            {"document_id": "D1", "namespace": "kb", "template_id": "TPL",
             "identity_hash": "h1", "version": 2},
        ])
        plan = await planner.plan(
            "documents",
            [{"document_id": "D1", "namespace": "kb", "template_id": "TPL",
              "identity_hash": "h1", "version": 1, "data": {"x": 1}}],
            "kb",
        )

        (clash,) = plan.clashes
        assert sorted(t["version"] for t in clash.targets) == [1, 2]

    @pytest.mark.asyncio
    async def test_projected_reads_do_not_produce_phantom_diffs(self):
        # Documents are read with a projection, so a field-level diff would
        # report every omitted field as a difference. Document clashes are
        # resolved by the on_clash policy, not by diffing.
        planner, _ = _planner("documents", [
            {"document_id": "D1", "namespace": "kb", "template_id": "TPL",
             "identity_hash": "h1", "version": 1, "data": {"x": 1}},
        ])
        plan = await planner.plan(
            "documents",
            [{"document_id": "D1", "namespace": "kb", "template_id": "TPL",
              "identity_hash": "h1", "version": 1, "data": {"x": 999}}],
            "kb",
        )

        (clash,) = plan.clashes
        assert clash.diff == {}
        assert "data" not in clash.target

    @pytest.mark.asyncio
    async def test_identity_less_documents_match_by_document_id_only(self):
        # Append-only templates have no logical identity — same document_id
        # means the same physical row; anything else is new.
        planner, _ = _planner("documents", [
            {"document_id": "D1", "namespace": "kb", "template_id": "TPL",
             "identity_hash": "", "version": 1},
        ])
        plan = await planner.plan(
            "documents",
            [
                {"document_id": "D1", "namespace": "kb", "template_id": "TPL",
                 "identity_hash": "", "version": 1},
                {"document_id": "D2", "namespace": "kb", "template_id": "TPL",
                 "identity_hash": "", "version": 1},
            ],
            "kb",
        )

        assert plan.conflicts == []
        assert len(plan.clashes) == 1
        assert [e["document_id"] for e in plan.to_insert] == ["D2"]


class TestBatching:
    @pytest.mark.asyncio
    async def test_probes_are_batched(self):
        entities = [
            {"terminology_id": f"T{i}", "namespace": "kb", "value": f"V{i}"}
            for i in range(KEY_BATCH_SIZE + 10)
        ]
        planner, collection = _planner("terminologies", [])
        await planner.plan("terminologies", entities, "kb")

        # Two key kinds (id and logical), each batched into 2 probes.
        assert len(collection.queries) == 4
        id_probe = collection.queries[0]
        assert len(id_probe["terminology_id"]["$in"]) == KEY_BATCH_SIZE

    @pytest.mark.asyncio
    async def test_no_entities_means_no_reads(self):
        planner, collection = _planner("terminologies", [])
        plan = await planner.plan("terminologies", [], "kb")
        assert collection.queries == []
        assert plan.summary() == {
            "insert": 0, "unchanged": 0, "clash": 0, "conflict": 0,
        }
