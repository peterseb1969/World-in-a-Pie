"""The shaping-transform core: rows travel verbatim, cut entities become
referents, one provenance hop records the input and the verified verdict."""

import json
import zipfile

import pytest
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.identity import compute_composite_key_hash, resolved_identity_values
from wip_archive.model import ArchiveModel
from wip_archive.transform import (
    OutputExistsError,
    file_sha256,
    run_transform,
)

NS = "demo"
LOV = "0190a000-0000-7000-0000-0000000000c1"
TERM_RED = "0190b000-0000-7000-0000-0000000000r1"
WIP_TERM = "0190b000-0000-7000-0000-00000000w001"
WIP_TERM_UNUSED = "0190b000-0000-7000-0000-00000000w002"
TPL_M = "0190c000-0000-7000-0000-0000000000m1"
TPL_S = "0190c000-0000-7000-0000-0000000000s1"
DOC_M = "019d0000-0000-7000-8000-0000000000m1"
DOC_S = "019d0000-0000-7000-8000-0000000000s1"
FILE_S = "019f0000-0000-7000-8000-0000000000f1"

SPELL_FIELDS = [{"name": "name", "type": "string", "mandatory": True}]
MONSTER_FIELDS = [
    {"name": "name", "type": "string", "mandatory": True},
    {"name": "colour", "type": "term", "terminology_ref": "COLOUR"},
    {"name": "spell", "type": "reference", "reference_type": "document",
     "target_templates": ["SPELL"]},
]


def _registry(entry_id, entity_type, value=None, namespace=NS):
    key = {"ns": namespace, "type": entity_type, "value": value or entry_id}
    return {
        "entry_id": entry_id, "namespace": namespace, "entity_type": entity_type,
        "primary_composite_key": key, "primary_composite_key_hash": "h-" + entry_id,
        "synonyms": [{"namespace": namespace, "entity_type": entity_type,
                      "composite_key": key}],
        "status": "active",
    }


def _hash(data, fields):
    return compute_composite_key_hash(resolved_identity_values(data, ["name"], fields, [], []))


def _spell():
    data = {"name": "Fireball"}
    return {
        "document_id": DOC_S, "namespace": NS, "template_id": TPL_S, "template_version": 1,
        "version": 1, "status": "active", "identity_hash": _hash(data, SPELL_FIELDS),
        "data": data, "term_references": [], "references": [],
        "file_references": [{"field_path": "scroll", "file_id": FILE_S}],
    }


def _monster(version, colour_ref=TERM_RED):
    data = {"name": "Smaug", "colour": "red", "spell": DOC_S}
    return {
        "document_id": DOC_M, "namespace": NS, "template_id": TPL_M, "template_version": 1,
        "version": version, "status": "active" if version == 2 else "inactive",
        "identity_hash": _hash(data, MONSTER_FIELDS), "data": data,
        "term_references": [
            {"field_path": "colour", "term_id": colour_ref},
            {"field_path": "aura", "term_id": WIP_TERM},
        ],
        "references": [{
            "field_path": "spell", "reference_type": "document",
            "resolved": {"document_id": DOC_S, "namespace": NS, "template_id": TPL_S},
        }],
        "file_references": [],
    }


def _rows():
    return {
        "terminologies": [{"terminology_id": LOV, "value": "COLOUR", "namespace": NS,
                           "case_sensitive": False, "status": "active"}],
        "terms": [{"term_id": TERM_RED, "terminology_id": LOV, "namespace": NS,
                   "value": "red", "status": "active", "names": []}],
        "templates": [
            {"template_id": TPL_S, "version": 1, "value": "SPELL", "namespace": NS,
             "status": "active", "identity_fields": ["name"], "fields": SPELL_FIELDS},
            {"template_id": TPL_M, "version": 1, "value": "MONSTER", "namespace": NS,
             "status": "active", "identity_fields": ["name"], "fields": MONSTER_FIELDS},
        ],
        "documents": [_spell(), _monster(1), _monster(2)],
        "files": [{"file_id": FILE_S, "namespace": NS, "filename": "scroll.txt",
                   "status": "active"}],
        "registry_entries": [
            _registry(LOV, "terminologies", "COLOUR"),
            _registry(TERM_RED, "terms", "red"),
            _registry(TPL_S, "templates", "SPELL"),
            _registry(TPL_M, "templates", "MONSTER"),
            _registry(DOC_S, "documents"),
            _registry(DOC_M, "documents"),
            _registry(FILE_S, "files"),
        ],
    }


def _manifest(counts_by_type, *, referents, extra=None):
    counts = {t: n for t, n in counts_by_type.items()}
    manifest = {
        "format_version": "3.1", "tool_version": "0.5.0",
        "exported_at": "2026-10-13T09:02:24Z", "source_host": "src",
        "namespaces": [{"prefix": NS, "counts": counts,
                        "provenance": [{"restore_job_id": "job-1", "mode": "restore"}]}],
        "namespace": NS, "include_files": True, "include_all_versions": True,
        "counts": counts, "referents": referents,
    }
    manifest.update(extra or {})
    return manifest


def _build(path, rows=None, *, extra=None, synonyms=None):
    rows = rows if rows is not None else _rows()
    writer = ArchiveWriter(path, default_namespace=NS)
    for entity_type, entities in rows.items():
        for entity in entities:
            writer.add_entity(entity_type, entity, namespace=NS)
    writer.add_blob(FILE_S, b"ancient words")
    referents = writer.write_referents_file([
        _registry(WIP_TERM, "terms", "glowing", namespace="wip"),
        _registry(WIP_TERM_UNUSED, "terms", "dim", namespace="wip"),
    ])
    if synonyms:
        writer.write_synonyms_file(synonyms)
    writer.write(_manifest({t: len(v) for t, v in rows.items()}, referents=referents,
                           extra=extra))
    return path


def _drop_spells(_ns, entity_type, row):
    if entity_type == "documents":
        return row["template_id"] != TPL_S
    return entity_type != "files"


def _keep_all(_ns, _entity_type, _row):
    return True


def _run(src, out, keep_row, **kwargs):
    return run_transform(src, out, transform="filter", toolkit_version="9.9.9",
                         params={"templates": ["MONSTER"], "boundary": "drop"},
                         keep_row=keep_row, **kwargs)


def _entities(path, entity_type):
    with ArchiveReader(path) as reader:
        return list(reader.read_entities(entity_type, namespace=NS))


def _referents(path):
    with ArchiveReader(path) as reader:
        return list(reader.read_referents())


def _raw_manifest(path):
    with ArchiveReader(path) as reader:
        return reader.read_manifest_raw()


@pytest.fixture
def src(tmp_path):
    return _build(tmp_path / "in.zip")


class TestACut:
    def test_kept_rows_travel_verbatim(self, src, tmp_path):
        out = tmp_path / "out.zip"
        _run(src, out, _drop_spells)
        assert _entities(out, "documents") == [_monster(1), _monster(2)]
        assert _entities(out, "templates") == _rows()["templates"]
        assert _entities(out, "files") == []

    def test_a_cut_entity_moves_from_the_registry_to_the_referents(self, src, tmp_path):
        out = tmp_path / "out.zip"
        _run(src, out, _drop_spells)
        carried = {r["entry_id"] for r in _entities(out, "registry_entries")}
        assert DOC_S not in carried and FILE_S not in carried
        assert carried == {LOV, TERM_RED, TPL_S, TPL_M, DOC_M}
        referents = {r["entry_id"]: r for r in _referents(out)}
        # The cut spell is referenced by the kept monster; the cut file is
        # referenced by nothing kept and leaves no trace. The wip term the
        # monster uses stays; the one only the input listed goes.
        assert set(referents) == {DOC_S, WIP_TERM}
        assert referents[DOC_S] == _registry(DOC_S, "documents")

    def test_the_manifest_is_the_inputs_with_counts_recomputed(self, src, tmp_path):
        out = tmp_path / "out.zip"
        _run(src, out, _drop_spells)
        manifest = _raw_manifest(out)
        assert manifest["counts"]["documents"] == 2
        assert manifest["counts"]["registry_entries"] == 5
        assert manifest["counts"]["files"] == 0
        assert manifest["namespaces"][0]["counts"]["documents"] == 2
        assert manifest["referents"] == 2
        # No blob travelled, so the archive no longer claims to carry files.
        assert manifest["include_files"] is False
        # Provenance rows and every other key travel unchanged.
        assert manifest["namespaces"][0]["provenance"] == [
            {"restore_job_id": "job-1", "mode": "restore"}
        ]
        assert manifest["source_host"] == "src"
        assert manifest["exported_at"] == "2026-10-13T09:02:24Z"
        with zipfile.ZipFile(out) as zf:
            assert not [n for n in zf.namelist() if n.startswith("blobs/")]

    def test_the_hop_records_input_params_verdict_and_counts(self, src, tmp_path):
        out = tmp_path / "out.zip"
        result = _run(src, out, _drop_spells, summary={"boundary_cuts": 1})
        chain = _raw_manifest(out)["derived_from"]
        assert chain == [result.hop]
        hop = result.hop
        assert hop["transform"] == "filter"
        assert hop["toolkit_version"] == "9.9.9"
        assert hop["at"].endswith("Z")
        assert hop["params"] == {"templates": ["MONSTER"], "boundary": "drop"}
        assert hop["params_redacted"] is False
        assert hop["input"] == {"sha256": file_sha256(src), "format_version": "3.1",
                                "derived": False}
        assert hop["verify"] == {"verdict": "pass",
                                 "findings": result.hop["verify"]["findings"]}
        assert hop["verify"]["findings"]["error"] == 0
        assert hop["verify"]["findings"]["warning"] == 0
        assert hop["summary"]["documents_kept"] == 2
        assert hop["summary"]["documents_dropped"] == 1
        assert hop["summary"]["files_dropped"] == 1
        assert hop["summary"]["referents"] == 2
        assert hop["summary"]["blobs_dropped"] == 1
        assert hop["summary"]["boundary_cuts"] == 1
        assert result.verdict == "pass"

    def test_a_reference_to_a_cut_document_is_not_dangling(self, src, tmp_path):
        out = tmp_path / "out.zip"
        _run(src, out, _drop_spells)
        with ArchiveReader(out) as reader:
            model = ArchiveModel.load(reader, check_documents=True)
        classes = {f.finding_class for f in model.findings()}
        assert "dangling_document_reference" not in classes
        assert any(e.target_id == DOC_S for e in model.external_refs)

    def test_without_the_referent_row_the_same_reference_dangles(self, tmp_path):
        rows = _rows()
        rows["documents"] = [_monster(1), _monster(2)]
        rows["registry_entries"] = [r for r in rows["registry_entries"]
                                    if r["entry_id"] != DOC_S]
        path = _build(tmp_path / "broken.zip", rows)
        with ArchiveReader(path) as reader:
            model = ArchiveModel.load(reader)
        assert "dangling_document_reference" in {f.finding_class for f in model.findings()}


class TestKeepingEverything:
    def test_an_identity_transform_changes_nothing_but_the_manifest(self, src, tmp_path):
        out = tmp_path / "out.zip"
        result = _run(src, out, _keep_all)
        for entity_type in ("documents", "templates", "terms", "terminologies", "files",
                            "registry_entries"):
            assert _entities(out, entity_type) == _entities(src, entity_type)
        # The unused wip referent was listed by the input only; nothing kept
        # references it, so it does not travel.
        assert {r["entry_id"] for r in _referents(out)} == {WIP_TERM}
        with ArchiveReader(out) as reader:
            assert reader.read_blob(FILE_S) == b"ancient words"
        assert _raw_manifest(out)["include_files"] is True
        assert result.hop["summary"]["blobs_kept"] == 1
        assert result.verdict == "pass"

    def test_keep_blob_drops_a_blob_whose_file_row_travels(self, src, tmp_path):
        out = tmp_path / "out.zip"
        result = _run(src, out, _keep_all, keep_blob=lambda _fid: False)
        assert [f["file_id"] for f in _entities(out, "files")] == [FILE_S]
        with ArchiveReader(out) as reader:
            assert reader.list_blobs() == []
        assert result.blobs_dropped == 1


class TestTheChain:
    def test_a_second_transform_appends_a_hop(self, src, tmp_path):
        first = tmp_path / "one.zip"
        second = tmp_path / "two.zip"
        one = _run(src, first, _drop_spells)
        two = run_transform(first, second, transform="latest-only", toolkit_version="9.9.9",
                            params={}, keep_row=lambda _n, t, r: t != "documents"
                            or r["version"] == 2)
        chain = _raw_manifest(second)["derived_from"]
        assert chain[0] == one.hop
        assert chain[1] == two.hop
        assert two.hop["input"]["derived"] is True
        assert two.hop["input"]["sha256"] == file_sha256(first)
        assert _entities(second, "documents") == [_monster(2)]

    def test_a_single_object_chain_is_read_as_one_hop(self, tmp_path):
        old_hop = {"transform": "filter", "verify": {"verdict": "pass"}}
        src = _build(tmp_path / "in.zip", extra={"derived_from": old_hop})
        out = tmp_path / "out.zip"
        result = _run(src, out, _keep_all)
        assert _raw_manifest(out)["derived_from"] == [old_hop, result.hop]
        assert result.hop["input"]["derived"] is True

    def test_unknown_manifest_keys_travel(self, tmp_path):
        src = _build(tmp_path / "in.zip", extra={"x_future": {"a": 1}})
        out = tmp_path / "out.zip"
        _run(src, out, _keep_all)
        assert _raw_manifest(out)["x_future"] == {"a": 1}


class TestRefusalsAndFailure:
    def test_an_existing_output_is_refused_without_overwrite(self, src, tmp_path):
        out = tmp_path / "out.zip"
        out.write_bytes(b"precious")
        with pytest.raises(OutputExistsError):
            _run(src, out, _keep_all)
        assert out.read_bytes() == b"precious"
        _run(src, out, _keep_all, overwrite=True)
        assert _raw_manifest(out)["derived_from"]

    def test_the_input_itself_is_refused_even_with_overwrite(self, src):
        before = file_sha256(src)
        with pytest.raises(OutputExistsError, match="input archive itself"):
            _run(src, src, _keep_all, overwrite=True)
        assert file_sha256(src) == before

    def test_cutting_a_template_its_documents_pin_is_a_warning(self, src, tmp_path):
        # The cut template becomes a referent: the door can resolve it on the
        # target, so its documents cannot be judged here, not judged broken.
        out = tmp_path / "out.zip"
        result = _run(src, out, lambda _n, t, r: not (t == "templates"
                                                      and r["template_id"] == TPL_M))
        assert result.verdict == "warn"
        assert TPL_M in {r["entry_id"] for r in _referents(out)}

    def test_a_failed_verify_still_writes_and_declares_it(self, tmp_path):
        # An input already broken: the monster pins a template version that
        # no archive row carries. Transforming it cannot repair that.
        rows = _rows()
        rows["documents"] = [_spell(), {**_monster(2), "template_version": 7}]
        src = _build(tmp_path / "in.zip", rows)
        out = tmp_path / "out.zip"
        result = _run(src, out, _keep_all)
        assert result.verdict == "fail"
        assert result.hop["verify"]["findings"]["error"] >= 1
        assert _raw_manifest(out)["derived_from"][-1]["verify"]["verdict"] == "fail"
        assert not (tmp_path / "out.zip.partial").exists()


class TestLegacySynonyms:
    def test_synonym_rows_follow_their_entities(self, tmp_path):
        synonyms = [
            {"entry_id": DOC_M, "composite_key": {"vendor": "M-1"}},
            {"entry_id": DOC_S, "composite_key": {"vendor": "S-1"}},
        ]
        src = _build(tmp_path / "in.zip", synonyms=synonyms)
        out = tmp_path / "out.zip"
        _run(src, out, _drop_spells)
        with ArchiveReader(out) as reader:
            assert list(reader.read_synonyms()) == [synonyms[0]]


def test_the_output_is_a_valid_zip_with_a_json_manifest(src, tmp_path):
    out = tmp_path / "out.zip"
    _run(src, out, _drop_spells)
    with zipfile.ZipFile(out) as zf:
        assert zf.testzip() is None
        json.loads(zf.read("manifest.json"))
        assert all(i.compress_type == zipfile.ZIP_DEFLATED for i in zf.infolist())
