"""The archive's full definitions: inheritance resolved as template-store does,
term names in both shapes, and the identity rows of referenced-but-not-carried
entities.
"""

import pytest
from test_model import _template, build_archive
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.definitions import (
    DefinitionIndex,
    UnresolvableTemplate,
    term_names,
)
from wip_archive.model import ArchiveModel
from wip_archive.models import EntityCounts, Manifest, NamespaceEntry

NS = "demo"
BASE = "0190c000-0000-7000-0000-0000000000b1"
CHILD = "0190c000-0000-7000-0000-0000000000c1"
GRANDCHILD = "0190c000-0000-7000-0000-0000000000g1"
OUTSIDE = "0190c000-0000-7000-0000-0000000000x1"


def _field(name, **kw):
    return {"name": name, "type": "string", **kw}


def _child(template_id, value, parent, parent_version, **kw):
    row = _template(template_id, value, extends=parent, **kw)
    row["extends_version"] = parent_version
    return row


def _index(templates):
    index = DefinitionIndex()
    for row in templates:
        index.add_template(row)
    return index


class TestInheritance:
    def test_a_template_without_extends_is_its_own_resolution(self):
        index = _index([_template(BASE, "BASE", fields=[_field("a")])])
        resolved = index.resolve_template(BASE, 1)
        assert [f["name"] for f in resolved.fields] == ["a"]
        assert resolved.identity_fields == ("name",)
        assert resolved.chain == ((BASE, 1),)

    def test_child_overrides_by_name_rules_concatenate_identity_from_child(self):
        base = _template(
            BASE, "BASE",
            fields=[_field("a", mandatory=True), _field("b")],
            identity_fields=("a",),
        )
        base["rules"] = [{"type": "dependency", "target_field": "a"}]
        child = _child(
            CHILD, "CHILD", BASE, 1,
            fields=[_field("b", mandatory=True), _field("c")],
            identity_fields=(),
        )
        child["rules"] = [{"type": "mutual_exclusion", "target_fields": ["b", "c"]}]
        resolved = _index([base, child]).resolve_template(CHILD, 1)

        # The overridden field keeps the parent's position, takes the child's definition.
        assert [f["name"] for f in resolved.fields] == ["a", "b", "c"]
        assert resolved.field("b")["mandatory"] is True
        assert [r["type"] for r in resolved.rules] == ["dependency", "mutual_exclusion"]
        # The child declares none, so the nearest ancestor's identity applies.
        assert resolved.identity_fields == ("a",)
        assert resolved.chain == ((BASE, 1), (CHILD, 1))

    def test_the_parent_is_taken_at_the_pinned_version_not_the_latest(self):
        v1 = _template(BASE, "BASE", fields=[_field("old")])
        v2 = _template(BASE, "BASE", version=2, fields=[_field("new")])
        child = _child(CHILD, "CHILD", BASE, 1)
        resolved = _index([v1, v2, child]).resolve_template(CHILD, 1)
        assert [f["name"] for f in resolved.fields] == ["old"]

    def test_a_parent_outside_the_archive_names_what_is_missing(self):
        index = _index([_child(CHILD, "CHILD", OUTSIDE, 3)])
        with pytest.raises(UnresolvableTemplate) as caught:
            index.resolve_template(CHILD, 1)
        assert caught.value.missing == (OUTSIDE, 3)

    def test_extends_without_a_pinned_version_is_unresolvable(self):
        index = _index([_template(BASE, "BASE"), _template(CHILD, "CHILD", extends=BASE)])
        with pytest.raises(UnresolvableTemplate, match="extends_version"):
            index.resolve_template(CHILD, 1)

    def test_circular_inheritance_is_unresolvable(self):
        a = _child(BASE, "A", CHILD, 1)
        b = _child(CHILD, "B", BASE, 1)
        with pytest.raises(UnresolvableTemplate, match="circular"):
            _index([a, b]).resolve_template(BASE, 1)

    def test_a_three_level_chain_resolves_root_to_child(self):
        base = _template(BASE, "BASE", fields=[_field("a")])
        child = _child(CHILD, "CHILD", BASE, 1, fields=[_field("b")])
        grandchild = _child(GRANDCHILD, "GRAND", CHILD, 1, fields=[_field("c")])
        resolved = _index([base, child, grandchild]).resolve_template(GRANDCHILD, 1)
        assert [f["name"] for f in resolved.fields] == ["a", "b", "c"]
        assert resolved.chain == ((BASE, 1), (CHILD, 1), (GRANDCHILD, 1))

    def test_as_template_dict_carries_the_resolution(self):
        base = _template(BASE, "BASE", fields=[_field("a")])
        child = _child(CHILD, "CHILD", BASE, 1, fields=[_field("b")], identity_fields=())
        resolved = _index([base, child]).resolve_template(CHILD, 1).as_template_dict()
        assert [f["name"] for f in resolved["fields"]] == ["a", "b"]
        assert resolved["identity_fields"] == ["name"]
        assert resolved["template_id"] == CHILD


class TestTermNames:
    def test_engine_names_and_legacy_aliases_are_both_names(self):
        assert term_names({"names": [{"value": "Crimson", "type": "SYNONYM"}, {"value": "R"}]}) == (
            "Crimson", "R",
        )
        assert term_names({"aliases": ["MR", "Mr."]}) == ("MR", "Mr.")
        assert term_names({"names": [{"value": "X"}], "aliases": ["X", "Y"]}) == ("X", "Y")
        assert term_names({"value": "Red"}) == ()


def _archive_with_referents(path, rows, referents):
    writer = ArchiveWriter(path, default_namespace=NS)
    counts = {}
    for entity_type, entities in rows.items():
        for entity in entities:
            writer.add_entity(entity_type, entity, namespace=NS)
        counts[entity_type] = len(entities)
    written = writer.write_referents_file(referents)
    entity_counts = EntityCounts(**counts)
    writer.write(
        Manifest(
            namespace=NS,
            counts=entity_counts,
            namespaces=[NamespaceEntry(prefix=NS, counts=entity_counts)],
            referents=written,
        )
    )
    return path


class TestLoadedFromAnArchive:
    def test_the_model_carries_full_definitions_and_referents(self, tmp_path):
        term = {
            "term_id": "term-red", "terminology_id": "lov-colour", "value": "Red",
            "status": "active", "names": [{"value": "Crimson", "type": "SYNONYM"}],
        }
        path = _archive_with_referents(
            tmp_path / "a.zip",
            {
                "terminologies": [{"terminology_id": "lov-colour", "value": "COLOUR",
                                   "case_sensitive": False, "status": "active"}],
                "terms": [term],
                "templates": [_template(BASE, "BASE", fields=[_field("a", pattern="^x")])],
            },
            [{"entry_id": OUTSIDE, "namespace": "wip", "entity_type": "templates",
              "primary_composite_key": {"value": "PERSON"}, "synonyms": []}],
        )
        with ArchiveReader(path) as reader:
            model = ArchiveModel.load(reader)

        definitions = model.definitions
        assert definitions.template(BASE, 1)["fields"][0]["pattern"] == "^x"
        assert definitions.terminologies["lov-colour"]["case_sensitive"] is False
        assert definitions.terms_by_terminology["lov-colour"] == [term]
        assert set(definitions.referents) == {OUTSIDE}
        # M1 read a nonexistent `aliases` key; engine terms carry `names`.
        assert model.terms["term-red"].aliases == ("Crimson",)

    def test_standalone_load_matches_the_model(self, tmp_path):
        path = build_archive(
            tmp_path / "b.zip",
            {"templates": [_template(BASE, "BASE"), _child(CHILD, "CHILD", BASE, 1)]},
        )
        with ArchiveReader(path) as reader:
            index = DefinitionIndex.load(reader)
        assert index.resolve_template(CHILD, 1).chain == ((BASE, 1), (CHILD, 1))
        assert index.referents == {}
