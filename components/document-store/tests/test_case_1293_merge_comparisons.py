"""A merge compares definitions by what they mean, not how they are stored.

Each test here reproduces a refusal seen on a live merge of an app's
namespace from one install into another, where the app had bootstrapped its
current schema as v1 on the target: a term's names compared as a list, a
template whose versions do not line up leaving every reference to it
unmapped, an edge type's derived endpoint constraint stored by an older
release, endpoint lists in another order, a server-filled terminology_value.
Plus the unique_names, absent-field and names-union rules that came with it.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from wip_archive.remap import IDRemapper

from document_store.services.backup_engine import COLLECTION_MAP
from document_store.services.merge_definitions import ABSENT, DefinitionsPlanner

NAMESPACE = "merge-1293-ns"


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


def _terminology(tid, value="APPS", **extra):
    return {"terminology_id": tid, "namespace": NAMESPACE, "value": value, **extra}


def _term(tid, terminology_id, value, names=(), **extra):
    return {
        "term_id": tid, "namespace": NAMESPACE, "terminology_id": terminology_id,
        "value": value,
        "names": [dict(n) if isinstance(n, dict) else n for n in names], **extra,
    }


def _template(tid, value, version=1, fields=None, **extra):
    return {
        "template_id": tid, "namespace": NAMESPACE, "value": value,
        "version": version,
        "fields": fields if fields is not None else [{"name": "title", "type": "string"}],
        **extra,
    }


def _edge(tid, value, source, target, *, field_targets=None, version=1):
    """An edge type whose endpoints are two-half entries {lookup_value, resolved}."""
    fields = [
        {"name": "source_ref", "type": "reference", "reference_type": "document",
         "target_templates": (field_targets or {}).get("source_ref")},
        {"name": "target_ref", "type": "reference", "reference_type": "document",
         "target_templates": (field_targets or {}).get("target_ref")},
    ]
    return _template(
        tid, value, version, fields=fields, usage="relationship",
        identity_fields=["source_ref", "target_ref"],
        source_templates=[{"lookup_value": v, "resolved": r} for v, r in source],
        target_templates=[{"lookup_value": v, "resolved": r} for v, r in target],
    )


# ---------------------------------------------------------------------------
# Term names
# ---------------------------------------------------------------------------


class TestTermNames:
    async def _plan(self, db, archive_names, target_names, **flags):
        await _seed(db, "terminologies", [_terminology("T-lov")])
        await _seed(db, "terms", [_term("T-term", "T-lov", "AuthorAssist", target_names)])
        return await _planner(db, **flags).plan(
            {
                "terminologies": [_terminology("A-lov")],
                "terms": [_term("A-term", "A-lov", "AuthorAssist", archive_names)],
            },
            NAMESPACE,
        )

    @pytest.mark.asyncio
    async def test_names_only_the_archive_has_do_not_refuse(self, db):
        plan = await self._plan(
            db, [{"value": "authorassist"}, {"value": "wip-aa"}], [{"value": "wip-aa"}],
        )

        assert plan.compatible, plan.incompatibilities
        assert [w.fields for w in plan.target_wins] == [["names"]]
        assert plan.extend_names == []

    @pytest.mark.asyncio
    async def test_names_in_another_order_are_the_same_names(self, db):
        plan = await self._plan(
            db,
            [{"value": "a"}, {"value": "b", "type": "ABBREVIATION"}],
            [{"value": "b", "type": "ABBREVIATION"}, {"value": "a"}],
        )

        assert plan.compatible
        assert plan.target_wins == []
        assert plan.unchanged["terms"] == 1

    @pytest.mark.asyncio
    async def test_extend_terminologies_adds_the_archives_names(self, db):
        plan = await self._plan(
            db, [{"value": "authorassist"}, {"value": "wip-aa"}], [{"value": "wip-aa"}],
            extend_terminologies=True,
        )

        assert plan.compatible
        assert len(plan.extend_names) == 1
        extension = plan.extend_names[0]
        assert extension["term_id"] == "T-term"
        assert [n["value"] for n in extension["names"]] == ["authorassist"]
        # The target ends up with every archive name, so nothing it keeps
        # differs from the archive's: no target-wins note for names.
        assert plan.target_wins == []

    @pytest.mark.asyncio
    async def test_the_targets_own_extra_names_are_reported_as_kept(self, db):
        plan = await self._plan(
            db, [{"value": "a"}], [{"value": "b"}], extend_terminologies=True,
        )

        assert [n["value"] for n in plan.extend_names[0]["names"]] == ["a"]
        assert [w.fields for w in plan.target_wins] == [["names"]]
        [note] = plan.target_wins
        assert "discarded" not in note.detail["names"]
        assert [n["value"] for n in note.detail["names"]["added"]] == ["a"]
        assert [n["value"] for n in note.detail["names"]["kept"]] == ["b"]

    @pytest.mark.asyncio
    async def test_a_names_description_difference_is_reported(self, db):
        plan = await self._plan(
            db,
            [{"value": "wip-aa", "description": "the app's synonym", "source": "RTiS"}],
            [{"value": "wip-aa"}],
            extend_terminologies=True,
        )

        assert plan.compatible, plan.incompatibilities
        assert plan.extend_names == []
        assert [w.fields for w in plan.target_wins] == [["names"]]

    @pytest.mark.asyncio
    async def test_a_bare_string_name_is_not_read_as_a_name(self, db):
        plan = await self._plan(
            db, ["legacy", {"value": "a"}], [], extend_terminologies=True,
        )

        assert plan.compatible, plan.incompatibilities
        assert [n["value"] for n in plan.extend_names[0]["names"]] == ["a"]

    @pytest.mark.asyncio
    async def test_an_added_name_never_makes_a_second_preferred_name(self, db):
        plan = await self._plan(
            db,
            [{"value": "a", "type": "SYNONYM", "preferred": True}],
            [{"value": "b", "type": "SYNONYM", "preferred": True}],
            extend_terminologies=True,
        )

        assert plan.extend_names[0]["names"][0]["preferred"] is False

    @pytest.mark.asyncio
    async def test_terminology_value_filled_by_the_server_is_not_compared(self, db):
        await _seed(db, "terminologies", [_terminology("T-lov")])
        await _seed(db, "terms", [
            _term("T-term", "T-lov", "M", terminology_value="APPS"),
        ])
        plan = await _planner(db).plan(
            {
                "terminologies": [_terminology("A-lov")],
                "terms": [_term("A-term", "A-lov", "M")],
            },
            NAMESPACE,
        )

        assert plan.compatible, plan.incompatibilities
        assert plan.unchanged["terms"] == 1


# ---------------------------------------------------------------------------
# unique_names, judged by effect
# ---------------------------------------------------------------------------


class TestUniqueNames:
    @pytest.mark.asyncio
    async def test_a_flag_difference_with_no_collision_does_not_refuse(self, db):
        await _seed(db, "terminologies", [_terminology("T-lov", unique_names=True)])
        await _seed(db, "terms", [_term("T-m", "T-lov", "M")])
        plan = await _planner(db, extend_terminologies=True).plan(
            {
                "terminologies": [_terminology("A-lov", unique_names=False)],
                "terms": [_term("A-m", "A-lov", "M"), _term("A-f", "A-lov", "F")],
            },
            NAMESPACE,
        )

        assert plan.compatible, plan.incompatibilities
        assert [w.fields for w in plan.target_wins] == [["unique_names"]]

    @pytest.mark.asyncio
    async def test_an_added_term_whose_name_another_term_bears_refuses(self, db):
        await _seed(db, "terminologies", [_terminology("T-lov", unique_names=True)])
        await _seed(db, "terms", [_term("T-m", "T-lov", "M", [{"value": "male"}])])
        plan = await _planner(db, extend_terminologies=True).plan(
            {
                "terminologies": [_terminology("A-lov", unique_names=False)],
                "terms": [
                    _term("A-m", "A-lov", "M", [{"value": "male"}]),
                    _term("A-x", "A-lov", "X", [{"value": "Male"}]),
                ],
            },
            NAMESPACE,
        )

        assert not plan.compatible
        [issue] = plan.incompatibilities
        assert issue.entity_type == "terminologies"
        assert issue.detail["unique_names"]["name_collisions"] == ["Male", "male"]

    @pytest.mark.asyncio
    async def test_a_name_two_target_terms_already_share_does_not_refuse(self, db):
        # The target's own duplicate (made before unique_names, or by a
        # write that bypassed it) is not the merge's doing: adding an
        # unrelated term must not be refused, nor blamed for it.
        await _seed(db, "terminologies", [_terminology("T-lov", unique_names=True)])
        await _seed(db, "terms", [
            _term("T-m", "T-lov", "M", [{"value": "male"}]),
            _term("T-x", "T-lov", "X", [{"value": "Male"}]),
        ])
        plan = await _planner(db, extend_terminologies=True).plan(
            {
                "terminologies": [_terminology("A-lov", unique_names=True)],
                "terms": [_term("A-f", "A-lov", "F", [{"value": "female"}])],
            },
            NAMESPACE,
        )

        assert plan.compatible, plan.incompatibilities

    @pytest.mark.asyncio
    async def test_a_name_added_to_a_shared_name_still_refuses(self, db):
        await _seed(db, "terminologies", [_terminology("T-lov", unique_names=True)])
        await _seed(db, "terms", [
            _term("T-m", "T-lov", "M", [{"value": "male"}]),
            _term("T-x", "T-lov", "X", [{"value": "Male"}]),
        ])
        plan = await _planner(db, extend_terminologies=True).plan(
            {
                "terminologies": [_terminology("A-lov", unique_names=True)],
                "terms": [_term("A-y", "A-lov", "Y", [{"value": "MALE"}])],
            },
            NAMESPACE,
        )

        [issue] = plan.incompatibilities
        assert issue.detail["unique_names"]["name_collisions"] == ["MALE", "Male", "male"]

    @pytest.mark.asyncio
    async def test_terms_are_read_in_the_terminologys_own_namespace(self, db):
        # Another namespace's term with the same terminology_id (ids are
        # unique per namespace only in the schema) is not this
        # terminology's term.
        await _seed(db, "terminologies", [_terminology("T-lov", unique_names=True)])
        db_name, coll_name = COLLECTION_MAP["terms"]
        other = {**_term("O-m", "T-lov", "M", [{"value": "male"}]), "namespace": "c1293-elsewhere"}
        await db[db_name][coll_name].insert_one(other)
        try:
            plan = await _planner(db, extend_terminologies=True).plan(
                {
                    "terminologies": [_terminology("A-lov", unique_names=True)],
                    "terms": [_term("A-x", "A-lov", "X", [{"value": "male"}])],
                },
                NAMESPACE,
            )
        finally:
            await db[db_name][coll_name].delete_many({"namespace": "c1293-elsewhere"})

        assert plan.compatible, plan.incompatibilities

    @pytest.mark.asyncio
    async def test_a_name_only_a_retired_term_bears_does_not_refuse(self, db):
        await _seed(db, "terminologies", [_terminology("T-lov", unique_names=True)])
        await _seed(db, "terms", [
            _term("T-m", "T-lov", "M", [{"value": "male"}], status="deprecated"),
        ])
        plan = await _planner(db, extend_terminologies=True).plan(
            {
                "terminologies": [_terminology("A-lov", unique_names=True)],
                "terms": [_term("A-x", "A-lov", "X", [{"value": "male"}])],
            },
            NAMESPACE,
        )

        assert plan.compatible, plan.incompatibilities

    @pytest.mark.asyncio
    async def test_a_name_the_merge_retires_does_not_refuse(self, db):
        await _seed(db, "terminologies", [_terminology("T-lov", unique_names=True)])
        await _seed(db, "terms", [_term("T-m", "T-lov", "M", [{"value": "male"}])])
        plan = await _planner(
            db, extend_terminologies=True, carry_retirements=True,
        ).plan(
            {
                "terminologies": [_terminology("A-lov", unique_names=True)],
                "terms": [
                    _term("A-m", "A-lov", "M", [{"value": "male"}], status="deprecated"),
                    _term("A-x", "A-lov", "X", [{"value": "male"}]),
                ],
            },
            NAMESPACE,
        )

        assert [r["term_id"] for r in plan.retire_terms] == ["T-m"]
        assert plan.compatible, plan.incompatibilities

    @pytest.mark.asyncio
    async def test_a_term_for_an_externally_mapped_terminology_is_checked(self, db):
        await _seed(db, "terminologies", [_terminology("T-lov", unique_names=True)])
        await _seed(db, "terms", [_term("T-m", "T-lov", "M", [{"value": "male"}])])
        external = IDRemapper()
        external.terminology_map["A-lov"] = "T-lov"
        plan = await _planner(db, extend_terminologies=True).plan(
            {"terms": [_term("A-x", "A-lov", "X", [{"value": "male"}])]},
            NAMESPACE,
            external=external,
        )

        assert not plan.compatible
        [issue] = plan.incompatibilities
        assert issue.detail["unique_names"]["name_collisions"] == ["male"]

    @pytest.mark.asyncio
    async def test_terms_added_to_a_terminology_the_merge_adds_are_checked(self, db):
        plan = await _planner(
            db, add_missing=True, extend_terminologies=True,
        ).plan(
            {
                "terminologies": [_terminology("A-lov", unique_names=True)],
                "terms": [
                    _term("A-m", "A-lov", "M", [{"value": "male"}]),
                    _term("A-x", "A-lov", "X", [{"value": "MALE"}]),
                ],
            },
            NAMESPACE,
        )

        assert not plan.compatible
        [issue] = plan.incompatibilities
        assert issue.detail["unique_names"]["name_collisions"] == ["MALE", "male"]

    @pytest.mark.asyncio
    async def test_names_are_folded_as_def_store_folds_them(self, db):
        await _seed(db, "terminologies", [_terminology("T-lov", unique_names=True)])
        await _seed(db, "terms", [_term("T-s", "T-lov", "S", [{"value": "Straße"}])])
        plan = await _planner(db, extend_terminologies=True).plan(
            {
                "terminologies": [_terminology("A-lov", unique_names=True)],
                "terms": [_term("A-x", "A-lov", "X", [{"value": "STRASSE"}])],
            },
            NAMESPACE,
        )

        assert plan.compatible, plan.incompatibilities

    @pytest.mark.asyncio
    async def test_without_unique_names_on_the_target_collisions_are_allowed(self, db):
        await _seed(db, "terminologies", [_terminology("T-lov")])
        await _seed(db, "terms", [_term("T-m", "T-lov", "M", [{"value": "male"}])])
        plan = await _planner(db, extend_terminologies=True).plan(
            {
                "terminologies": [_terminology("A-lov", unique_names=True)],
                "terms": [
                    _term("A-m", "A-lov", "M", [{"value": "male"}]),
                    _term("A-x", "A-lov", "X", [{"value": "male"}]),
                ],
            },
            NAMESPACE,
        )

        assert plan.compatible, plan.incompatibilities


# ---------------------------------------------------------------------------
# Template references and versions that do not line up
# ---------------------------------------------------------------------------


class TestTemplateMapping:
    @pytest.mark.asyncio
    async def test_a_reference_to_a_template_with_no_common_version_maps(self, db):
        # The archive's DESIGN_DECISION runs v2..v3 (its v1 is gone); the
        # target bootstrapped its own as v1. The edge type DECIDED_BY is v1
        # on both sides and names DESIGN_DECISION by each side's id.
        await _seed(db, "templates", [
            _template("T-dd", "DESIGN_DECISION", 1),
            _template("T-fs", "FIRESIDE", 1),
            _edge("T-by", "DECIDED_BY", [("DESIGN_DECISION", "T-dd")],
                  [("FIRESIDE", "T-fs")]),
        ])
        plan = await _planner(db).plan(
            {"templates": [
                _template("A-dd", "DESIGN_DECISION", 2),
                _template("A-dd", "DESIGN_DECISION", 3),
                _template("A-fs", "FIRESIDE", 1),
                _edge("A-by", "DECIDED_BY", [("DESIGN_DECISION", "A-dd")],
                      [("FIRESIDE", "A-fs")]),
            ]},
            NAMESPACE,
        )

        assert plan.mapping["templates"]["A-dd"] == "T-dd"
        names = [i.name for i in plan.incompatibilities]
        # DECIDED_BY compares equal once DESIGN_DECISION's id is mapped.
        assert "DECIDED_BY v1" not in names
        # DESIGN_DECISION itself is refused once, naming align_templates.
        assert names == ["DESIGN_DECISION"]
        [issue] = plan.incompatibilities
        assert "align_templates" in issue.reason
        assert issue.detail["versions"] == {"archive": [2, 3], "target": [1]}

    @pytest.mark.asyncio
    async def test_no_common_version_is_refused_even_under_add_missing(self, db):
        await _seed(db, "templates", [_template("T-dd", "DESIGN_DECISION", 1)])
        plan = await _planner(db, add_missing=True).plan(
            {"templates": [_template("A-dd", "DESIGN_DECISION", 2)]}, NAMESPACE,
        )

        assert [i.name for i in plan.incompatibilities] == ["DESIGN_DECISION"]
        assert plan.to_add["templates"] == []

    @pytest.mark.asyncio
    async def test_a_later_version_still_extends_a_matching_history(self, db):
        await _seed(db, "templates", [_template("T-p", "PATIENT", 1)])
        plan = await _planner(db, add_missing=True).plan(
            {"templates": [
                _template("A-p", "PATIENT", 1),
                _template("A-p", "PATIENT", 2, fields=[{"name": "x", "type": "string"}]),
            ]},
            NAMESPACE,
        )

        assert plan.compatible, plan.incompatibilities
        assert [r["version"] for r in plan.to_add["templates"]] == [2]
        assert plan.mapping["templates"] == {"A-p": "T-p"}

    @pytest.mark.asyncio
    async def test_endpoint_lists_in_another_order_are_equal(self, db):
        await _seed(db, "templates", [
            _template("T-a", "A"), _template("T-b", "B"),
            _edge("T-e", "LINKS", [("A", "T-a"), ("B", "T-b")], [("A", "T-a")]),
        ])
        plan = await _planner(db).plan(
            {"templates": [
                _template("T-a", "A"), _template("T-b", "B"),
                _edge("T-e", "LINKS", [("B", "T-b"), ("A", "T-a")], [("A", "T-a")]),
            ]},
            NAMESPACE,
        )

        assert plan.compatible, plan.incompatibilities

    @pytest.mark.asyncio
    async def test_a_stored_edge_endpoint_constraint_is_not_compared(self, db):
        # An older release stored source_ref/target_ref target_templates on
        # the edge type; the target derives them when it serves the template.
        await _seed(db, "templates", [
            _template("T-a", "A"),
            _edge("T-e", "LINKS", [("A", "T-a")], [("A", "T-a")]),
        ])
        plan = await _planner(db).plan(
            {"templates": [
                _template("T-a", "A"),
                _edge("T-e", "LINKS", [("A", "T-a")], [("A", "T-a")],
                      field_targets={"source_ref": ["T-a"], "target_ref": ["A"]}),
            ]},
            NAMESPACE,
        )

        assert plan.compatible, plan.incompatibilities

    @pytest.mark.asyncio
    async def test_a_legacy_bare_endpoint_compares_by_the_template_it_names(self, db):
        await _seed(db, "templates", [
            _template("T-a", "A"),
            _edge("T-e", "LINKS", [("A", "T-a")], [("A", "T-a")]),
        ])
        legacy = _edge("T-e", "LINKS", [], [])
        legacy["source_templates"] = ["A"]
        legacy["target_templates"] = ["A"]
        plan = await _planner(db).plan(
            {"templates": [_template("T-a", "A"), legacy]}, NAMESPACE,
        )

        assert plan.compatible, plan.incompatibilities

    @pytest.mark.asyncio
    async def test_an_unresolved_qualified_endpoint_names_the_template(self, db):
        await _seed(db, "templates", [
            _template("T-a", "A"),
            _edge("T-e", "LINKS", [("A", "T-a")], [("A", "T-a")]),
        ])
        plan = await _planner(db).plan(
            {"templates": [
                _template("T-a", "A"),
                _edge("T-e", "LINKS", [(f"{NAMESPACE}:A", None)], [("A", "T-a")]),
            ]},
            NAMESPACE,
        )

        assert plan.compatible, plan.incompatibilities

    @pytest.mark.asyncio
    async def test_a_renamed_endpoint_still_refuses(self, db):
        await _seed(db, "templates", [
            _template("T-a", "A"), _template("T-kb", "KB_BOOTSTRAP_RECORD"),
            _edge("T-e", "LINKS", [("KB_BOOTSTRAP_RECORD", "T-kb")], [("A", "T-a")]),
        ])
        plan = await _planner(db, add_missing=True).plan(
            {"templates": [
                _template("T-a", "A"), _template("A-b", "BOOTSTRAP_RECORD"),
                _edge("T-e", "LINKS", [("BOOTSTRAP_RECORD", "A-b")], [("A", "T-a")]),
            ]},
            NAMESPACE,
        )

        assert [i.name for i in plan.incompatibilities] == ["LINKS v1"]

    @pytest.mark.asyncio
    async def test_a_plain_fields_target_templates_compare_as_a_set(self, db):
        def flag(targets):
            return _template("X", "FLAG", fields=[{
                "name": "flagged", "type": "reference", "reference_type": "document",
                "target_templates": targets,
            }])

        await _seed(db, "templates", [
            _template("T-a", "A"), _template("T-b", "B"), flag(["T-a", "T-b"]),
        ])
        plan = await _planner(db).plan(
            {"templates": [_template("T-a", "A"), _template("T-b", "B"), flag(["T-b", "A"])]},
            NAMESPACE,
        )

        assert plan.compatible, plan.incompatibilities


# ---------------------------------------------------------------------------
# The refusal says what each side stored
# ---------------------------------------------------------------------------


class TestAbsentInDetail:
    @pytest.mark.asyncio
    async def test_a_field_the_archive_row_lacks_is_reported_absent(self, db):
        await _seed(db, "terminologies", [_terminology("T-lov", case_sensitive=True)])
        plan = await _planner(db).plan(
            {"terminologies": [_terminology("A-lov")]}, NAMESPACE,
        )

        [issue] = plan.incompatibilities
        assert issue.detail["case_sensitive"] == {"target": True, "archive": ABSENT}
