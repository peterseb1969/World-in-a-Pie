"""verify checks a document's template- and terminology-typed references.

A target the archive carries — in any of its namespaces — or records as a
referent is fine; one that is neither, in an archive with a referents table,
is a `missing_template_reference` / `missing_terminology_reference` error,
like a missing term. The check runs even when the document's own template
cannot be judged. inspect reports such a target as an outside reference.
"""

from test_definitions import _archive_with_referents
from test_document_check import LOV, NS, TPL, _doc, _rows
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.model import ArchiveModel
from wip_archive.models import EntityCounts, Manifest, NamespaceEntry

GONE_TPL = "0190c000-0000-7000-0000-0000000000g1"
GONE_LOV = "0190a000-0000-7000-0000-0000000000g2"


def _named(doc, *, template=None, terminology=None):
    refs = []
    if template:
        refs.append({"field_path": "kind", "reference_type": "template",
                     "resolved": {"template_id": template}})
    if terminology:
        refs.append({"field_path": "vocab", "reference_type": "terminology",
                     "resolved": {"terminology_id": terminology}})
    doc["references"] = refs
    return doc


def _findings(path, namespaces=None):
    with ArchiveReader(path) as reader:
        model = ArchiveModel.load(reader, namespaces=namespaces, check_documents=True)
    return model, {f.finding_class: f for f in model.findings()}


def test_targets_neither_carried_nor_referents_are_missing(tmp_path):
    rows = _rows([_named(_doc("d1", {"code": "A"}), template=GONE_TPL, terminology=GONE_LOV)])
    model, findings = _findings(_archive_with_referents(tmp_path / "a.zip", rows, []))
    assert findings["missing_template_reference"].severity == "error"
    assert findings["missing_template_reference"].count == 1
    assert findings["missing_terminology_reference"].count == 1
    checks = model.to_dict()["document_checks"]
    assert checks["missing_template_references"] == 1
    assert checks["missing_terminology_references"] == 1


def test_carried_targets_and_referents_are_not_missing(tmp_path):
    rows = _rows([
        _named(_doc("d1", {"code": "A"}), template=TPL, terminology=LOV),
        _named(_doc("d2", {"code": "B"}), template=GONE_TPL, terminology=GONE_LOV),
    ])
    referents = [
        {"entry_id": GONE_TPL, "entity_type": "templates", "namespace": "wip"},
        {"entry_id": GONE_LOV, "entity_type": "terminologies", "namespace": "wip"},
    ]
    _, findings = _findings(_archive_with_referents(tmp_path / "a.zip", rows, referents))
    assert "missing_template_reference" not in findings
    assert "missing_terminology_reference" not in findings


def test_targets_carried_in_an_unselected_namespace_are_not_missing(tmp_path):
    rows = _rows([_named(_doc("d1", {"code": "A"}), template=GONE_TPL, terminology=GONE_LOV)])
    path = tmp_path / "multi.zip"
    writer = ArchiveWriter(path, default_namespace=NS)
    for entity_type, entities in rows.items():
        for entity in entities:
            writer.add_entity(entity_type, entity, namespace=NS)
    writer.add_entity("templates", {"template_id": GONE_TPL, "version": 1, "value": "SHARED",
                                    "namespace": "wip", "status": "active", "fields": []},
                      namespace="wip")
    writer.add_entity("terminologies", {"terminology_id": GONE_LOV, "value": "SHARED_LOV",
                                        "namespace": "wip", "status": "active"}, namespace="wip")
    referents = writer.write_referents_file([])
    writer.write(Manifest(
        namespace=NS, counts=EntityCounts(),
        namespaces=[NamespaceEntry(prefix=NS, counts=EntityCounts()),
                    NamespaceEntry(prefix="wip", counts=EntityCounts(templates=1, terminologies=1))],
        referents=referents,
    ))
    _, findings = _findings(path, namespaces=[NS])
    assert "missing_template_reference" not in findings
    assert "missing_terminology_reference" not in findings


def test_references_are_checked_when_the_documents_template_cannot_be_judged(tmp_path):
    """A document on a template the archive does not carry: its schema is
    unverifiable, but its references' targets are still checked."""
    doc = _named(_doc("d1", {"code": "A"}, template_id="0190c000-0000-7000-0000-00000000none"),
                 template=GONE_TPL)
    doc["term_references"] = [{"field_path": "colour", "term_id": "t-nowhere"}]
    _, findings = _findings(_archive_with_referents(tmp_path / "a.zip", _rows([doc]), []))
    assert "unverifiable_dependency" in findings
    assert "missing_template_reference" in findings
    assert "missing_term_reference" in findings


def test_inspect_reports_an_uncarried_target_as_an_outside_reference(tmp_path):
    rows = _rows([_named(_doc("d1", {"code": "A"}), template=GONE_TPL, terminology=GONE_LOV)])
    with ArchiveReader(_archive_with_referents(tmp_path / "a.zip", rows, [])) as reader:
        model = ArchiveModel.load(reader)
    external = {(e.kind, e.target_id) for e in model.external_refs}
    assert ("template", GONE_TPL) in external
    assert ("terminology", GONE_LOV) in external
