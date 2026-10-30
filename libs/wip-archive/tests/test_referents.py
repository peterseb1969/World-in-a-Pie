"""The out-of-archive referent table: what is collected, and how it travels."""

from wip_archive.archive import REFERENTS_FILE, ArchiveReader, ArchiveWriter
from wip_archive.models import Manifest
from wip_archive.referents import ReferentCollector, referenced_ids


class TestReferencedIds:
    """Every slot a restore remaps is a slot the table must cover."""

    def test_a_term_names_its_terminology_and_parent(self):
        row = {"terminology_id": "TY", "parent_term_id": "TP", "value": "x"}
        assert set(referenced_ids("terms", row)) == {"TY", "TP"}

    def test_a_relation_names_both_terms(self):
        row = {"source_term_id": "S", "target_term_id": "T", "relation_type": "is_a"}
        assert set(referenced_ids("term_relations", row)) == {"S", "T"}

    def test_a_template_names_every_field_reference_its_parent_and_endpoints(self):
        row = {
            "extends": "PARENT",
            "fields": [
                {"name": "a", "terminology_ref": "TY1"},
                {"name": "b", "array_terminology_ref": "TY2"},
                {"name": "c", "template_ref": "TPL1"},
                {"name": "d", "array_template_ref": "TPL2"},
                {"name": "e", "target_templates": ["TPL3"], "target_terminologies": ["TY3"]},
                {"name": "plain"},
            ],
            "source_templates": [{"lookup_value": "SRC", "resolved": "SRC-ID"}],
            "target_templates": ["LEGACY-TGT", {"lookup_value": "x", "resolved": None}],
        }
        assert set(referenced_ids("templates", row)) == {
            "PARENT", "TY1", "TY2", "TPL1", "TPL2", "TPL3", "TY3",
            "SRC-ID", "LEGACY-TGT",
        }

    def test_a_document_names_its_template_and_every_reference(self):
        row = {
            "template_id": "TPL",
            "term_references": [{"field_path": "g", "term_id": "TERM", "terminology_ref": "TY"}],
            "references": [
                {"reference_type": "document",
                 "resolved": {"document_id": "DOC", "template_id": "TPL2", "namespace": "ns"}},
                {"reference_type": "term", "resolved": {"term_id": "TERM2", "terminology_id": "TY2"}},
            ],
            "file_references": [{"field_path": "scan", "file_id": "FILE"}],
        }
        assert set(referenced_ids("documents", row)) == {
            "TPL", "TERM", "TY", "DOC", "TPL2", "TERM2", "TY2", "FILE",
        }

    def test_types_without_references_yield_nothing(self):
        for entity_type in ("terminologies", "files", "registry_entries"):
            assert list(referenced_ids(entity_type, {"terminology_id": "X"})) == []


class TestReferentCollector:
    def test_carried_ids_are_subtracted(self):
        collector = ReferentCollector()
        collector.add("documents", {"template_id": "TPL", "references": [
            {"resolved": {"document_id": "OUT"}},
        ]})
        collector.add("registry_entries", {"entry_id": "TPL"})
        assert collector.external_ids() == ["OUT"]

    def test_order_of_rows_does_not_matter_and_output_is_sorted(self):
        collector = ReferentCollector()
        collector.add("registry_entries", {"entry_id": "B"})
        collector.add("term_relations", {"source_term_id": "C", "target_term_id": "A"})
        collector.add("term_relations", {"source_term_id": "B", "target_term_id": "A"})
        assert collector.external_ids() == ["A", "C"]


class TestReferentsInTheArchive:
    def test_the_table_round_trips(self, tmp_path):
        output = tmp_path / "a.zip"
        writer = ArchiveWriter(output, default_namespace="kb")
        rows = [
            {"entry_id": "A", "namespace": "wip", "entity_type": "terminologies",
             "primary_composite_key": {"ns": "wip", "type": "terminology", "value": "GENDER"}},
        ]
        assert writer.write_referents_file(rows) == 1
        writer.write(Manifest(namespace="kb", referents=1))

        with ArchiveReader(output) as reader:
            assert reader.read_manifest().referents == 1
            assert list(reader.read_referents()) == rows

    def test_an_empty_table_writes_no_file(self, tmp_path):
        output = tmp_path / "a.zip"
        writer = ArchiveWriter(output, default_namespace="kb")
        assert writer.write_referents_file([]) == 0
        writer.write(Manifest(namespace="kb", referents=0))

        with ArchiveReader(output) as reader:
            assert REFERENTS_FILE not in reader.namelist()
            assert list(reader.read_referents()) == []
            assert reader.read_manifest().referents == 0

    def test_an_archive_from_before_the_table_reads_as_none(self):
        # Older manifests have no key: None, never 0, so a restore knows the
        # table was not recorded rather than empty.
        assert Manifest.model_validate({"namespace": "kb"}).referents is None
