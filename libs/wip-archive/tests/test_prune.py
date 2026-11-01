"""The `prune` plan: templates nothing uses go, with the terminologies only
they used; everything holding data stays."""

import pytest
from test_filter import BIG, LOV_COLOUR, LOV_SIZE, MONSTER, NS, SPELL, _id, _registry, _rows, _write
from wip_archive.archive import ArchiveReader
from wip_archive.model import ArchiveModel
from wip_archive.prune import plan_prune
from wip_archive.transform import run_transform

ORPHAN, CYCLE_A, CYCLE_B = _id("c", 21), _id("c", 22), _id("c", 23)
LOV_UNDECLARED, LOV_RESCUED, LOV_SHARED = _id("a", 21), _id("a", 22), _id("a", 23)
TERM_RESCUED, TERM_SHARED = _id("b", 21), _id("b", 22)


def _tpl(template_id, value, fields):
    return {"template_id": template_id, "version": 1, "value": value, "namespace": NS,
            "status": "active", "identity_fields": [], "fields": fields}


def _term_field(name, terminology):
    return {"name": name, "type": "term", "terminology_ref": terminology}


def _lov(terminology_id, value):
    return {"terminology_id": terminology_id, "value": value, "namespace": NS,
            "status": "active"}


@pytest.fixture
def archive(tmp_path):
    rows = _rows()
    rows["templates"] += [
        # Nothing pins it; it alone declares SIZE and RESCUED, and shares SHARED.
        _tpl(ORPHAN, "ORPHAN", [_term_field("size", "SIZE"), _term_field("r", "RESCUED"),
                                _term_field("s", "SHARED")]),
        # Two templates without documents that only declare each other.
        _tpl(CYCLE_A, "CYCLE_A", [{"name": "b", "type": "reference",
                                   "reference_type": "document", "target_templates": ["CYCLE_B"]}]),
        _tpl(CYCLE_B, "CYCLE_B", [{"name": "a", "type": "reference",
                                   "reference_type": "document", "target_templates": ["CYCLE_A"]}]),
    ]
    # MONSTER (which has documents) also declares SHARED.
    monster = next(t for t in rows["templates"] if t["template_id"] == MONSTER)
    monster["fields"] = [*monster["fields"], _term_field("mood", "SHARED")]
    rows["terminologies"] += [_lov(LOV_UNDECLARED, "UNDECLARED"), _lov(LOV_RESCUED, "RESCUED"),
                              _lov(LOV_SHARED, "SHARED")]
    rows["terms"] += [
        {"term_id": TERM_RESCUED, "terminology_id": LOV_RESCUED, "namespace": NS,
         "value": "calm", "status": "active", "names": []},
        {"term_id": TERM_SHARED, "terminology_id": LOV_SHARED, "namespace": NS,
         "value": "grim", "status": "active", "names": []},
    ]
    # A relation from the kept COLOUR into RESCUED keeps RESCUED.
    rows["term_relations"].append({"source_term_id": _rows()["terms"][0]["term_id"],
                                   "target_term_id": TERM_RESCUED,
                                   "relation_type": "related_to", "namespace": NS})
    rows["registry_entries"] += [
        *(_registry(i, "templates") for i in (ORPHAN, CYCLE_A, CYCLE_B)),
        *(_registry(i, "terminologies") for i in (LOV_UNDECLARED, LOV_RESCUED, LOV_SHARED)),
        *(_registry(i, "terms") for i in (TERM_RESCUED, TERM_SHARED)),
    ]
    return _write(tmp_path / "in.zip", {NS: rows})


def _plan(path):
    with ArchiveReader(path) as reader:
        return plan_prune(reader, ArchiveModel.load(reader))


def test_templates_without_data_or_a_user_are_pruned(archive):
    plan = _plan(archive)
    assert plan.templates == {ORPHAN, CYCLE_A, CYCLE_B}
    assert plan.template_names == [f"{NS}:CYCLE_A", f"{NS}:CYCLE_B", f"{NS}:ORPHAN"]


def test_a_template_without_documents_that_a_used_schema_declares_stays(archive):
    # SPELL has documents too, but MONSTER's schema would keep it regardless.
    assert SPELL not in _plan(archive).templates


def test_only_terminologies_the_pruned_templates_alone_used_go(archive):
    plan = _plan(archive)
    # SIZE: declared only by ORPHAN. SHARED: MONSTER declares it too.
    # RESCUED: a relation from the kept COLOUR points into it. UNDECLARED:
    # no template ever declared it. COLOUR: in use.
    assert plan.terminologies == {LOV_SIZE}
    assert plan.terms == {BIG}
    assert plan.terminology_names == [f"{NS}:SIZE"]


def test_applied_the_output_verifies_and_leaves_no_referent(archive, tmp_path):
    out = tmp_path / "out.zip"
    with ArchiveReader(archive) as reader:
        plan = plan_prune(reader, ArchiveModel.load(reader))
    result = run_transform(archive, out, transform="prune", toolkit_version="t", params={},
                           keep_row=plan.keep_row, summary=plan.summary())
    assert result.verdict == "pass", [f.to_dict() for f in result.model.findings()]
    assert result.referents == 0
    assert result.dropped == {"templates": 3, "terminologies": 1, "terms": 1,
                              "term_relations": 1, "registry_entries": 5}
    assert result.hop["summary"]["templates_pruned"] == plan.template_names
    with ArchiveReader(out) as reader:
        kept = {t["template_id"] for t in reader.read_entities("templates", namespace=NS)}
        lovs = {t["terminology_id"] for t in reader.read_entities("terminologies", namespace=NS)}
    assert ORPHAN not in kept and MONSTER in kept
    assert lovs == {LOV_COLOUR, LOV_UNDECLARED, LOV_RESCUED, LOV_SHARED}


def test_an_archive_with_nothing_unused_prunes_nothing(tmp_path):
    path = _write(tmp_path / "in.zip", {NS: _rows()})
    plan = _plan(path)
    assert plan.templates == set() and plan.terminologies == set()
