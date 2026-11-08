"""Outside references are counted, not kept per document version.

The model promises memory that does not grow with the archive's references.
An app backup whose documents reference shared `wip` terms has one such
reference per document version; keeping one object for each would break the
promise on the ordinary path. These tests pin the aggregate: one entry per
(kind, target, reason, referring template) with a count, per closure variant,
so the latest-only closure lists only what latest versions reference.
"""

from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.model import SCHEMA_VERSION, ArchiveModel
from wip_archive.models import EntityCounts, Manifest, NamespaceEntry

NS = "app"
TPL = "0190c000-0000-7000-0000-0000000000a1"
WIP_TERM = "0190d000-0000-7000-0000-00000000t001"
CUT_TERM = "0190d000-0000-7000-0000-00000000t002"
OLD_FILE = "0190e000-0000-7000-0000-00000000f001"


def _template():
    return {
        "template_id": TPL, "version": 1, "value": "NOTE", "label": "Note",
        "usage": "entity", "versioned": True, "status": "active",
        "identity_fields": ["name"], "fields": [],
    }


def _doc(doc_id, version, *, terms=(), files=()):
    return {
        "document_id": doc_id, "template_id": TPL, "template_version": 1,
        "version": version, "identity_hash": f"h-{doc_id}", "data": {},
        "references": [],
        "term_references": [{"field_path": "kind", "term_id": t} for t in terms],
        "file_references": [{"field_path": "att", "file_id": f} for f in files],
    }


def _registry(entry_id, entity_type):
    return {
        "entry_id": entry_id, "namespace": NS, "entity_type": entity_type,
        "primary_composite_key": {}, "primary_composite_key_hash": "h",
        "synonyms": [], "status": "active",
    }


def _referent(entry_id, namespace, entity_type):
    return {
        "entry_id": entry_id, "namespace": namespace, "entity_type": entity_type,
        "primary_composite_key": {}, "synonyms": [], "status": "active",
    }


def _archive(path, documents, *, referents=None):
    writer = ArchiveWriter(path, default_namespace=NS)
    rows = {
        "templates": [_template()],
        "documents": documents,
        "registry_entries": [_registry(TPL, "templates")]
        + [_registry(d["document_id"], "documents") for d in documents if d["version"] == 1],
    }
    for entity_type, entities in rows.items():
        for entity in entities:
            writer.add_entity(entity_type, entity, namespace=NS)
    count = None
    if referents is not None:
        count = writer.write_referents_file(referents)
    counts = EntityCounts(**{k: len(v) for k, v in rows.items()})
    writer.write(Manifest(
        namespace=NS, counts=counts, referents=count,
        namespaces=[NamespaceEntry(prefix=NS, counts=counts)],
    ))
    with ArchiveReader(path) as reader:
        return ArchiveModel.load(reader)


def test_one_entry_per_target_and_template_however_many_versions_reference_it(tmp_path):
    # Ten documents, three versions each, all referencing the same outside term.
    docs = [
        _doc(f"d{i}", v, terms=[WIP_TERM]) for i in range(10) for v in (1, 2, 3)
    ]
    model = _archive(tmp_path / "a.zip", docs)

    assert len(model.external_refs) == 1
    entry = model.external_refs[0]
    assert (entry.kind, entry.target_id, entry.origin_template) == ("term", WIP_TERM, TPL)
    assert entry.count == 30
    # The finding still reports every occurrence.
    finding = next(f for f in model.findings() if f.finding_class == "external_reference")
    assert finding.count == 30
    assert finding.samples == ("term:30",)


def test_latest_only_closure_lists_only_what_latest_versions_reference(tmp_path):
    # v1 holds a file that v2 dropped.
    docs = [_doc("d1", 1, files=[OLD_FILE]), _doc("d1", 2)]
    model = _archive(tmp_path / "b.zip", docs)

    history = model.data_closure({TPL})
    latest = model.data_closure({TPL}, latest_only=True)
    assert [(e.kind, e.target_id) for e in history.external] == [("file", OLD_FILE)]
    assert latest.external == []


def test_term_reason_follows_the_referent_table(tmp_path):
    docs = [_doc("d1", 1, terms=[WIP_TERM, CUT_TERM])]
    model = _archive(
        tmp_path / "c.zip",
        docs,
        referents=[
            _referent(WIP_TERM, "wip", "terms"),  # another namespace
            _referent(CUT_TERM, NS, "terms"),     # this namespace, cut by a transform
        ],
    )
    reasons = {e.target_id: e.reason for e in model.external_refs}
    assert reasons == {
        WIP_TERM: "other-namespace-not-in-archive",
        CUT_TERM: "not-in-archive",
    }


def test_json_lists_each_outside_target_once_with_its_count(tmp_path):
    docs = [_doc(f"d{i}", v, terms=[WIP_TERM]) for i in range(3) for v in (1, 2)]
    model = _archive(tmp_path / "d.zip", docs)

    payload = model.to_dict()
    assert payload["schema"] == SCHEMA_VERSION == "1.3"
    report = next(t for t in payload["templates"] if t["template_id"] == TPL)
    history = report["data_closure"]["external"]
    latest = report["data_closure_latest_only"]["external"]
    assert [(e["target_id"], e["count"], e["origin_template"]) for e in history] == [
        (WIP_TERM, 6, TPL)
    ]
    assert [(e["target_id"], e["count"]) for e in latest] == [(WIP_TERM, 3)]


def test_dangling_references_are_aggregated_too(tmp_path):
    ref = {
        "field_path": "friend", "reference_type": "document", "lookup_value": "ghost",
        "resolved": {"document_id": "ghost", "namespace": NS, "template_id": TPL},
    }
    docs = []
    for v in (1, 2, 3):
        doc = _doc("d1", v)
        doc["references"] = [ref]
        docs.append(doc)
    model = _archive(tmp_path / "e.zip", docs)

    assert len(model.dangling) == 1
    assert model.dangling[0].count == 3
    finding = next(
        f for f in model.findings() if f.finding_class == "dangling_document_reference"
    )
    assert finding.count == 3
    assert finding.samples == ("document d1",)
