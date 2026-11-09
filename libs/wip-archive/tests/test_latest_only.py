"""The `latest-only` plan: newest versions, the versions pinned references
name, and files that only dropped versions referenced."""

import pytest
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.latest_only import plan_latest_only
from wip_archive.model import ArchiveModel
from wip_archive.transform import run_transform

NS = "demo"
TPL = "0190c000-0000-7000-0000-0000000000t1"
A, B, C = (f"019d0000-0000-7000-8000-00000000000{n}" for n in "abc")
F_OLD, F_NEW, F_LOOSE = (f"019f0000-0000-7000-8000-00000000000{n}" for n in "123")


def _ref(target, version, strategy):
    return {"field_path": "other", "reference_type": "document", "version_strategy": strategy,
            "resolved": {"document_id": target, "version": version, "namespace": NS,
                         "template_id": TPL}}


def _doc(doc_id, version, *, status="inactive", refs=(), files=()):
    return {"document_id": doc_id, "namespace": NS, "template_id": TPL, "template_version": 1,
            "version": version, "status": status, "identity_hash": "", "data": {"other": None},
            "term_references": [], "references": list(refs),
            "file_references": [{"field_path": "f", "file_id": f} for f in files]}


def _registry(entry_id, entity_type):
    return {"entry_id": entry_id, "namespace": NS, "entity_type": entity_type,
            "primary_composite_key": {}, "primary_composite_key_hash": "h", "synonyms": [],
            "status": "active"}


@pytest.fixture
def archive(tmp_path):
    documents = [
        _doc(A, 1, refs=[_ref(C, 1, "pinned")]),
        _doc(A, 2, files=[F_OLD]),
        # A's newest version is retired: it stays, retired.
        _doc(A, 3, files=[F_NEW]),
        _doc(B, 1),
        # B's newest pins A v1 (which pins C v1), and names A v2 by "latest".
        _doc(B, 2, status="active", refs=[_ref(A, 1, "pinned"), _ref(A, 2, "latest")]),
        _doc(C, 1),
        _doc(C, 2, status="active"),
    ]
    rows = {
        "templates": [{"template_id": TPL, "version": 1, "value": "ITEM", "namespace": NS,
                       "status": "active", "identity_fields": [],
                       "fields": [{"name": "other", "type": "reference",
                                   "reference_type": "document"}]}],
        "documents": documents,
        "files": [{"file_id": f, "namespace": NS, "status": "active"}
                  for f in (F_OLD, F_NEW, F_LOOSE)],
        "registry_entries": [_registry(TPL, "templates"),
                             *(_registry(d, "documents") for d in (A, B, C)),
                             *(_registry(f, "files") for f in (F_OLD, F_NEW, F_LOOSE))],
    }
    path = tmp_path / "in.zip"
    writer = ArchiveWriter(path, default_namespace=NS)
    for entity_type, entities in rows.items():
        for entity in entities:
            writer.add_entity(entity_type, entity, namespace=NS)
    for f in (F_OLD, F_NEW, F_LOOSE):
        writer.add_blob(f, f.encode())
    writer.write({"format_version": "3.1", "namespace": NS, "include_files": True,
                  "include_all_versions": True, "counts": {},
                  "namespaces": [{"prefix": NS, "counts": {}}],
                  "referents": writer.write_referents_file([])})
    return path


def _plan(path):
    with ArchiveReader(path) as reader:
        return plan_latest_only(reader, ArchiveModel.load(reader))


def test_newest_versions_and_pinned_versions_travel(archive):
    plan = _plan(archive)
    assert plan.rows == {(A, 3), (B, 2), (C, 2), (A, 1), (C, 1)}
    # A v1 is pinned by B v2; C v1 by A v1, a version kept only for that pin.
    assert plan.pinned_versions_kept == 2
    assert plan.versions_dropped == 2  # A v2 and B v1


def test_a_latest_reference_does_not_keep_an_old_version(archive):
    assert (A, 2) not in _plan(archive).rows


def test_a_file_only_dropped_versions_referenced_leaves(archive):
    # F_OLD only by A v2 (dropped); F_NEW by A v3; F_LOOSE by nothing at all.
    assert _plan(archive).files_dropped == {F_OLD}


def test_applied_the_output_says_it_is_not_all_versions(archive, tmp_path):
    out = tmp_path / "out.zip"
    with ArchiveReader(archive) as reader:
        plan = plan_latest_only(reader, ArchiveModel.load(reader))
    result = run_transform(archive, out, transform="latest-only", toolkit_version="t",
                           params={}, keep_row=plan.keep_row, summary=plan.summary(),
                           manifest_updates={"include_all_versions": False})
    assert result.verdict != "fail", [f.to_dict() for f in result.model.findings()]
    with ArchiveReader(out) as reader:
        manifest = reader.read_manifest_raw()
        rows = [(d["document_id"], d["version"], d["status"])
                for d in reader.read_entities("documents", namespace=NS)]
        assert sorted(reader.list_blobs()) == sorted([F_NEW, F_LOOSE])
    assert manifest["include_all_versions"] is False
    assert (A, 3, "inactive") in rows
    assert result.hop["summary"]["versions_dropped"] == 2
    assert result.hop["summary"]["pinned_versions_kept"] == 2
    # The dropped file takes its Registry row with it.
    assert result.dropped == {"documents": 2, "files": 1, "registry_entries": 1}


D, E, G = (f"019d0000-0000-7000-8000-0000000000{n}" for n in ("d1", "e1", "g1"))


def _write(path, documents):
    writer = ArchiveWriter(path, default_namespace=NS)
    writer.add_entity("templates", {"template_id": TPL, "version": 1, "value": "ITEM",
                                    "namespace": NS, "status": "active",
                                    "identity_fields": [], "fields": []}, namespace=NS)
    for doc in documents:
        writer.add_entity("documents", doc, namespace=NS)
    writer.write({"format_version": "3.1", "namespace": NS, "include_all_versions": True,
                  "counts": {}, "namespaces": [{"prefix": NS, "counts": {}}],
                  "referents": writer.write_referents_file([])})
    return path


def test_a_version_the_model_counts_is_one_the_plan_keeps(tmp_path):
    # The model counts a missing, null or zero version as 1 and a numeric
    # string as its integer. The plan keys rows the same way: a document
    # whose newest row is written "3" keeps that row (and drops 1 and 2),
    # and a single-row document with no usable version is kept, not lost.
    no_version = _doc(E, 1)
    del no_version["version"]
    documents = [_doc(D, 1), _doc(D, 2), _doc(D, "3", status="active"),
                 no_version, _doc(G, 0, status="active")]
    path = _write(tmp_path / "in.zip", documents)

    plan = _plan(path)

    kept = [d for d in documents if plan.keep_row(NS, "documents", d)]
    assert [(d["document_id"], d.get("version")) for d in kept] == [
        (D, "3"), (E, None), (G, 0),
    ]
    assert plan.versions_dropped == 2


def test_a_pin_written_as_a_string_keeps_the_version_it_names(tmp_path):
    documents = [_doc(A, 1), _doc(A, 2, status="active"),
                 _doc(B, 1, status="active", refs=[_ref(A, "1", "pinned")])]
    plan = _plan(_write(tmp_path / "in.zip", documents))
    assert (A, 1) in plan.rows
    assert plan.pinned_versions_kept == 1
