"""The `filter` plan: selection by template, relationships with their source,
the four boundary policies, and definitions that always close."""

import pytest
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.filter import BoundaryViolation, FilterError, plan_filter
from wip_archive.identity import compute_composite_key_hash, resolved_identity_values
from wip_archive.model import ArchiveModel
from wip_archive.transform import run_transform

NS = "demo"
OTHER = "other"


def _id(kind, n):
    return f"019{kind}0000-0000-7000-8000-{n:012d}"


LOV_COLOUR, LOV_SIZE = _id("a", 1), _id("a", 2)
RED, BLUE, BIG = _id("b", 1), _id("b", 2), _id("b", 3)
SPELL, MONSTER, KNOWS, LAIR = _id("c", 1), _id("c", 2), _id("c", 3), _id("c", 4)
OTHER_SPELL = _id("c", 9)
S1, S2, M1, M2, K1, L1 = (_id("d", n) for n in range(1, 7))
F1 = _id("f", 1)

NAME = {"name": "name", "type": "string", "mandatory": True}


def _ref(name, target):
    return {"name": name, "type": "reference", "reference_type": "document",
            "target_templates": [target]}


FIELDS = {
    SPELL: [NAME],
    MONSTER: [NAME, {"name": "colour", "type": "term", "terminology_ref": "COLOUR"},
              _ref("favourite", "SPELL")],
    KNOWS: [_ref("source_ref", "MONSTER"), _ref("target_ref", "SPELL")],
    LAIR: [NAME, _ref("resident", "MONSTER")],
}


def _template(template_id, value, **extra):
    return {"template_id": template_id, "version": 1, "value": value, "namespace": NS,
            "status": "active", "identity_fields": ["name"] if template_id != KNOWS
            else ["source_ref", "target_ref"], "fields": FIELDS[template_id], **extra}


def _reference(field_path, target, template_id):
    return {"field_path": field_path, "reference_type": "document",
            "resolved": {"document_id": target, "namespace": NS, "template_id": template_id}}


def _doc(doc_id, template_id, data, *, version=1, refs=(), terms=(), files=()):
    refs = list(refs)
    identity = ["name"] if template_id != KNOWS else ["source_ref", "target_ref"]
    hash_ = compute_composite_key_hash(resolved_identity_values(
        data, identity, FIELDS[template_id], list(terms), refs))
    return {"document_id": doc_id, "namespace": NS, "template_id": template_id,
            "template_version": 1, "version": version, "status": "active",
            "identity_hash": hash_, "data": data, "term_references": list(terms),
            "references": refs, "file_references": list(files)}


def _registry(entry_id, entity_type, namespace=NS):
    key = {"ns": namespace, "type": entity_type, "value": entry_id}
    return {"entry_id": entry_id, "namespace": namespace, "entity_type": entity_type,
            "primary_composite_key": key, "primary_composite_key_hash": "h",
            "synonyms": [], "status": "active"}


def _rows():
    red_ref = {"field_path": "colour", "term_id": RED}
    docs = [
        _doc(S1, SPELL, {"name": "Fireball"}),
        _doc(S2, SPELL, {"name": "Frost Nova"}),
        _doc(M1, MONSTER, {"name": "Smaug", "colour": "red", "favourite": S1},
             refs=[_reference("favourite", S1, SPELL)], terms=[red_ref],
             files=[{"field_path": "portrait", "file_id": F1}]),
        _doc(M1, MONSTER, {"name": "Smaug", "colour": "red", "favourite": S1}, version=2,
             refs=[_reference("favourite", S1, SPELL)], terms=[red_ref]),
        _doc(M2, MONSTER, {"name": "Glaurung"}),
        _doc(K1, KNOWS, {"source_ref": M1, "target_ref": S2},
             refs=[_reference("source_ref", M1, MONSTER), _reference("target_ref", S2, SPELL)]),
        _doc(L1, LAIR, {"name": "Erebor", "resident": M1},
             refs=[_reference("resident", M1, MONSTER)]),
    ]
    return {
        "terminologies": [
            {"terminology_id": LOV_COLOUR, "value": "COLOUR", "namespace": NS, "status": "active"},
            {"terminology_id": LOV_SIZE, "value": "SIZE", "namespace": NS, "status": "active"},
        ],
        "terms": [
            {"term_id": RED, "terminology_id": LOV_COLOUR, "namespace": NS, "value": "red",
             "status": "active", "names": []},
            {"term_id": BLUE, "terminology_id": LOV_COLOUR, "namespace": NS, "value": "blue",
             "status": "active", "names": []},
            {"term_id": BIG, "terminology_id": LOV_SIZE, "namespace": NS, "value": "big",
             "status": "active", "names": []},
        ],
        "term_relations": [
            {"source_term_id": BLUE, "target_term_id": RED, "relation_type": "related_to",
             "namespace": NS},
            {"source_term_id": BIG, "target_term_id": RED, "relation_type": "related_to",
             "namespace": NS},
        ],
        "templates": [
            _template(SPELL, "SPELL"),
            _template(MONSTER, "MONSTER"),
            _template(KNOWS, "KNOWS", usage="relationship", versioned=False,
                      source_templates=["MONSTER"], target_templates=["SPELL"]),
            _template(LAIR, "LAIR"),
        ],
        "documents": docs,
        "files": [{"file_id": F1, "namespace": NS, "filename": "smaug.png", "status": "active"}],
        "registry_entries": [
            *(_registry(i, "terminologies") for i in (LOV_COLOUR, LOV_SIZE)),
            *(_registry(i, "terms") for i in (RED, BLUE, BIG)),
            *(_registry(i, "templates") for i in (SPELL, MONSTER, KNOWS, LAIR)),
            *(_registry(i, "documents") for i in (S1, S2, M1, M2, K1, L1)),
            _registry(F1, "files"),
        ],
    }


def _write(path, namespaces):
    writer = ArchiveWriter(path)
    entries = []
    for ns, rows in namespaces.items():
        for entity_type, entities in rows.items():
            for entity in entities:
                writer.add_entity(entity_type, entity, namespace=ns)
        entries.append({"prefix": ns, "counts": {t: len(v) for t, v in rows.items()}})
    referents = writer.write_referents_file([])
    writer.write({"format_version": "3.1", "namespaces": entries,
                  "namespace": NS if len(entries) == 1 else "",
                  "include_files": False, "counts": {}, "referents": referents})
    return path


@pytest.fixture
def archive(tmp_path):
    return _write(tmp_path / "in.zip", {NS: _rows()})


def _plan(path, names, **kwargs):
    with ArchiveReader(path) as reader:
        model = ArchiveModel.load(reader)
        return plan_filter(reader, model, names, **kwargs)


class TestSelection:
    def test_drop_keeps_the_selection_and_its_relationships(self, archive):
        plan = _plan(archive, ["MONSTER"])
        assert plan.documents == {M1, M2, K1}
        assert plan.selected_documents == 2 and plan.relationship_documents == 1
        assert plan.cut_targets == {S1, S2}
        assert plan.cut_references == 2
        # Definitions close: MONSTER declares SPELL; K1 pins KNOWS. LAIR,
        # which only references MONSTER, stays behind.
        assert plan.templates == {MONSTER, SPELL, KNOWS}
        assert plan.terminologies == {LOV_COLOUR}
        assert plan.terms == {RED, BLUE}
        assert plan.files == {F1}

    def test_relationships_can_be_left_behind(self, archive):
        plan = _plan(archive, ["MONSTER"], relationships=False)
        assert plan.documents == {M1, M2}
        assert plan.relationship_documents == 0
        assert KNOWS not in plan.templates
        assert plan.cut_targets == {S1}

    def test_a_relationship_comes_only_with_its_source(self, archive):
        # SPELL documents are relationship targets, not sources.
        plan = _plan(archive, ["SPELL"])
        assert plan.documents == {S1, S2}
        assert plan.relationship_documents == 0

    def test_names_resolve_by_id_and_value(self, archive):
        assert _plan(archive, [MONSTER]).documents == _plan(archive, ["MONSTER"]).documents

    def test_an_unknown_name_is_refused(self, archive):
        with pytest.raises(FilterError, match="no template 'DRAGON'"):
            _plan(archive, ["DRAGON"])

    def test_no_names_without_definitions_only_is_refused(self, archive):
        with pytest.raises(FilterError, match="at least one template"):
            _plan(archive, [])

    def test_an_unknown_boundary_is_refused(self, archive):
        with pytest.raises(FilterError, match="unknown boundary"):
            _plan(archive, ["MONSTER"], boundary="loose")


class TestNamesAcrossNamespaces:
    @pytest.fixture
    def two(self, tmp_path):
        other = {"templates": [{"template_id": OTHER_SPELL, "version": 1, "value": "SPELL",
                                "namespace": OTHER, "status": "active", "fields": [NAME]}],
                 "registry_entries": [_registry(OTHER_SPELL, "templates", OTHER)]}
        return _write(tmp_path / "two.zip", {NS: _rows(), OTHER: other})

    def test_a_bare_value_in_two_namespaces_is_refused(self, two):
        with pytest.raises(FilterError, match="several namespaces"):
            _plan(two, ["SPELL"])

    def test_ns_value_picks_one(self, two):
        plan = _plan(two, [f"{OTHER}:SPELL"], definitions_only=True)
        assert plan.templates == {OTHER_SPELL}


class TestBoundaries:
    def test_strict_refuses_a_crossing(self, archive):
        with pytest.raises(BoundaryViolation) as refused:
            _plan(archive, ["MONSTER"], boundary="strict")
        assert refused.value.plan.cut_references == 2

    def test_strict_accepts_an_island(self, archive):
        assert _plan(archive, ["SPELL"], boundary="strict").cut_references == 0

    def test_stubs_go_one_hop(self, archive):
        plan = _plan(archive, ["LAIR"], boundary="stubs")
        assert plan.documents == {L1, M1}
        assert plan.added_documents == 1
        # The stub's own reference crosses.
        assert plan.cut_targets == {S1}
        assert {MONSTER, SPELL, LAIR} <= plan.templates

    def test_closure_follows_until_nothing_crosses(self, archive):
        plan = _plan(archive, ["LAIR"], boundary="closure")
        # Relationships follow the template selection only, so K1 (sourced at
        # the pulled-in M1) does not come.
        assert plan.documents == {L1, M1, S1}
        assert plan.added_documents == 2
        assert plan.cut_references == 0


class TestDefinitionsOnly:
    def test_no_names_keeps_every_definition_and_no_document(self, archive):
        plan = _plan(archive, [], definitions_only=True)
        assert plan.documents == set() and plan.files == set()
        assert plan.templates == {SPELL, MONSTER, KNOWS, LAIR}
        assert plan.terminologies == {LOV_COLOUR, LOV_SIZE}
        assert plan.terms == {RED, BLUE, BIG}

    def test_names_keep_their_closure(self, archive):
        plan = _plan(archive, ["MONSTER"], definitions_only=True)
        assert plan.templates == {MONSTER, SPELL}
        assert plan.terminologies == {LOV_COLOUR}
        assert plan.documents == set()


class TestApplied:
    def _apply(self, archive, out, names, **kwargs):
        with ArchiveReader(archive) as reader:
            model = ArchiveModel.load(reader)
            plan = plan_filter(reader, model, names, **kwargs)
        result = run_transform(archive, out, transform="filter", toolkit_version="t",
                               params={"templates": names}, keep_row=plan.keep_row,
                               summary=plan.summary())
        return plan, result

    def test_a_dropped_filter_verifies_and_records_its_cuts(self, archive, tmp_path):
        out = tmp_path / "out.zip"
        _, result = self._apply(archive, out, ["MONSTER"])
        assert result.verdict == "pass", [f.to_dict() for f in result.model.findings()]
        with ArchiveReader(out) as reader:
            referents = {r["entry_id"] for r in reader.read_referents()}
            relations = list(reader.read_entities("term_relations", namespace=NS))
            carried = {r["entry_id"] for r in reader.read_entities("registry_entries",
                                                                   namespace=NS)}
        assert referents == {S1, S2}
        assert LAIR not in carried and L1 not in carried and LAIR not in referents
        assert [r["source_term_id"] for r in relations] == [BLUE]
        summary = result.hop["summary"]
        assert summary["selected_documents"] == 2
        assert summary["relationship_documents"] == 1
        assert summary["cut_documents"] == 2
        assert summary["documents_kept"] == 4  # M1 twice: every version travels

    def test_a_namespace_nothing_keeps_is_not_listed(self, tmp_path):
        other = {"templates": [{"template_id": OTHER_SPELL, "version": 1, "value": "SPELL",
                                "namespace": OTHER, "status": "active", "fields": [NAME]}],
                 "registry_entries": [_registry(OTHER_SPELL, "templates", OTHER)]}
        two = _write(tmp_path / "two.zip", {NS: _rows(), OTHER: other})
        out = tmp_path / "out.zip"
        self._apply(two, out, [f"{NS}:MONSTER"])
        with ArchiveReader(out) as reader:
            manifest = reader.read_manifest_raw()
        assert [e["prefix"] for e in manifest["namespaces"]] == [NS]
        assert manifest["namespace"] == NS
