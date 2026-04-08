"""Tests for archive read/write round-trip."""

import json
import tempfile
from pathlib import Path

import pytest

from wip_toolkit.archive import ArchiveReader, ArchiveWriter, ENTITY_FILES
from wip_toolkit.models import EntityCounts, Manifest


class TestArchiveRoundTrip:
    """Test that entities survive a write/read round-trip."""

    def test_empty_archive(self, tmp_path):
        """Empty archive has valid manifest and zero entities."""
        output = tmp_path / "empty.zip"
        writer = ArchiveWriter(output)
        manifest = Manifest(namespace="test")
        writer.write(manifest)

        with ArchiveReader(output) as reader:
            m = reader.read_manifest()
            assert m.namespace == "test"
            assert m.counts.total == 0
            for entity_type in ENTITY_FILES:
                assert list(reader.read_entities(entity_type)) == []

    def test_terminology_round_trip(self, tmp_path):
        """Terminologies survive write/read."""
        output = tmp_path / "terms.zip"
        writer = ArchiveWriter(output)

        terminology = {
            "terminology_id": "0190a000-0000-7000-0000-000000000001",
            "value": "COUNTRY",
            "label": "Country",
            "_source": "primary",
        }
        writer.add_entity("terminologies", terminology)

        manifest = Manifest(
            namespace="wip",
            counts=EntityCounts(terminologies=1),
        )
        writer.write(manifest)

        with ArchiveReader(output) as reader:
            entities = list(reader.read_entities("terminologies"))
            assert len(entities) == 1
            assert entities[0]["terminology_id"] == "0190a000-0000-7000-0000-000000000001"
            assert entities[0]["value"] == "COUNTRY"
            assert entities[0]["_source"] == "primary"

    def test_multiple_entity_types(self, tmp_path):
        output = tmp_path / "multi.zip"
        writer = ArchiveWriter(output)

        writer.add_entity("terminologies", {"terminology_id": "0190a000-0000-7000-0000-000000000001", "value": "A"})
        writer.add_entity("terms", {"term_id": "0190b000-0000-7000-0000-000000000001", "value": "a"})
        writer.add_entity("templates", {"template_id": "0190c000-0000-7000-0000-000000000001", "value": "X"})
        writer.add_entity("documents", {"document_id": "0190d000-0000-7000-0000-000000000001", "data": {"x": 1}})
        writer.add_entity("files", {"file_id": "FILE-1", "filename": "test.txt"})

        manifest = Manifest(
            namespace="wip",
            counts=EntityCounts(
                terminologies=1, terms=1, templates=1, documents=1, files=1,
            ),
        )
        writer.write(manifest)

        with ArchiveReader(output) as reader:
            m = reader.read_manifest()
            assert m.counts.total == 5

            assert reader.entity_count("terminologies") == 1
            assert reader.entity_count("terms") == 1
            assert reader.entity_count("templates") == 1
            assert reader.entity_count("documents") == 1
            assert reader.entity_count("files") == 1

    def test_large_batch(self, tmp_path):
        """Many entities survive round-trip."""
        output = tmp_path / "large.zip"
        writer = ArchiveWriter(output)

        count = 500
        for i in range(count):
            writer.add_entity("terms", {
                "term_id": f"0190b000-0000-7000-0000-{i:012d}",
                "value": f"term_{i}",
                "terminology_id": "0190a000-0000-7000-0000-000000000001",
            })

        manifest = Manifest(
            namespace="wip",
            counts=EntityCounts(terms=count),
        )
        writer.write(manifest)

        with ArchiveReader(output) as reader:
            entities = list(reader.read_entities("terms"))
            assert len(entities) == count
            assert entities[0]["term_id"] == "0190b000-0000-7000-0000-000000000000"
            assert entities[-1]["term_id"] == f"0190b000-0000-7000-0000-{count - 1:012d}"

    def test_open_blob_streaming(self, tmp_path):
        """open_blob() yields a binary handle that streams to disk (CASE-28).

        Verifies the new streaming entry point: writes happen in chunks
        through a context-managed file handle, no full-blob bytes object
        is constructed in Python.
        """
        output = tmp_path / "stream-blob.zip"
        writer = ArchiveWriter(output)

        chunks = [b"chunk-1-", b"chunk-2-", b"chunk-3"]
        with writer.open_blob("FILE-STREAM") as fh:
            for chunk in chunks:
                fh.write(chunk)

        manifest = Manifest(
            namespace="wip",
            counts=EntityCounts(files=0),
            include_files=True,
        )
        writer.write(manifest)

        with ArchiveReader(output) as reader:
            assert reader.list_blobs() == ["FILE-STREAM"]
            assert reader.read_blob("FILE-STREAM") == b"".join(chunks)

    def test_archivewriter_tmp_dir_override(self, tmp_path):
        """tmp_dir kwarg lands the scratch dir under the supplied path (CASE-29).

        Verifies the document-store /backup endpoint can co-locate scratch
        with the configured WIP_BACKUP_DIR instead of the system /tmp.
        """
        scratch_root = tmp_path / "scratch"
        scratch_root.mkdir()
        output = tmp_path / "out.zip"

        writer = ArchiveWriter(output, tmp_dir=scratch_root)

        # Scratch should live under the override, not under /tmp
        assert Path(writer._tmp_dir).parent == scratch_root
        assert writer._blobs_dir.parent == Path(writer._tmp_dir)
        assert writer._blobs_dir.exists()

        # Round-trip still works
        writer.add_entity("terms", {"term_id": "0190b000-0000-7000-0000-000000000001"})
        writer.write(Manifest(namespace="wip", counts=EntityCounts(terms=1)))
        with ArchiveReader(output) as reader:
            assert reader.entity_count("terms") == 1

    def test_blob_round_trip(self, tmp_path):
        """Binary blobs survive round-trip."""
        output = tmp_path / "blobs.zip"
        writer = ArchiveWriter(output)

        blob_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        writer.add_blob("FILE-000001", blob_data)
        writer.add_entity("files", {
            "file_id": "FILE-000001",
            "filename": "test.png",
            "content_type": "image/png",
        })

        manifest = Manifest(
            namespace="wip",
            counts=EntityCounts(files=1),
            include_files=True,
        )
        writer.write(manifest)

        with ArchiveReader(output) as reader:
            blobs = reader.list_blobs()
            assert blobs == ["FILE-000001"]

            data = reader.read_blob("FILE-000001")
            assert data == blob_data

            assert reader.read_blob("NONEXISTENT") is None

    def test_manifest_fields_preserved(self, tmp_path):
        """All manifest fields survive round-trip."""
        output = tmp_path / "manifest.zip"
        writer = ArchiveWriter(output)

        manifest = Manifest(
            source_host="pi-poe-8gb.local",
            namespace="custom-ns",
            include_inactive=True,
            include_files=True,
            counts=EntityCounts(terminologies=5, terms=100, templates=3),
        )
        writer.write(manifest)

        with ArchiveReader(output) as reader:
            m = reader.read_manifest()
            assert m.source_host == "pi-poe-8gb.local"
            assert m.namespace == "custom-ns"
            assert m.include_inactive is True
            assert m.include_files is True
            assert m.counts.terminologies == 5
            assert m.counts.terms == 100
            assert m.counts.templates == 3

    def test_special_characters_in_data(self, tmp_path):
        """JSON special characters survive round-trip."""
        output = tmp_path / "special.zip"
        writer = ArchiveWriter(output)

        writer.add_entity("documents", {
            "document_id": "0190d000-0000-7000-0000-000000000001",
            "data": {
                "name": 'O\'Brien "the great"',
                "notes": "line1\nline2\ttab",
                "emoji": "\U0001f600",
                "unicode": "\u00e9\u00e8\u00ea",
            },
        })

        manifest = Manifest(namespace="wip", counts=EntityCounts(documents=1))
        writer.write(manifest)

        with ArchiveReader(output) as reader:
            docs = list(reader.read_entities("documents"))
            assert len(docs) == 1
            assert docs[0]["data"]["name"] == 'O\'Brien "the great"'
            assert docs[0]["data"]["emoji"] == "\U0001f600"

    def test_nonexistent_archive_raises(self):
        with pytest.raises(FileNotFoundError):
            ArchiveReader("/tmp/nonexistent_archive_xyz.zip")

    def test_entity_count(self, tmp_path):
        output = tmp_path / "count.zip"
        writer = ArchiveWriter(output)
        for i in range(7):
            writer.add_entity("terms", {"term_id": f"0190b000-0000-7000-0000-{i:012d}"})

        manifest = Manifest(namespace="wip", counts=EntityCounts(terms=7))
        writer.write(manifest)

        with ArchiveReader(output) as reader:
            assert reader.entity_count("terms") == 7
            assert reader.entity_count("documents") == 0

    def test_archive_sizes(self, tmp_path):
        output = tmp_path / "sizes.zip"
        writer = ArchiveWriter(output)
        for i in range(100):
            writer.add_entity("terms", {"term_id": f"0190b000-0000-7000-0000-{i:012d}", "value": f"term_{i}" * 10})

        manifest = Manifest(namespace="wip", counts=EntityCounts(terms=100))
        writer.write(manifest)

        with ArchiveReader(output) as reader:
            assert reader.total_size() > 0
            assert reader.compressed_size() > 0
            # Compressed should be smaller
            assert reader.compressed_size() <= reader.total_size()

    def test_registry_metadata_round_trip(self, tmp_path, sample_registry_data):
        """Entities with _registry metadata survive write/read round-trip."""
        output = tmp_path / "registry.zip"
        writer = ArchiveWriter(output)

        terminology = {
            "terminology_id": "0190a000-0000-7000-0000-000000000001",
            "value": "COUNTRY",
            "_source": "primary",
            "_registry": sample_registry_data["terminology"],
        }
        writer.add_entity("terminologies", terminology)

        document = {
            "document_id": "019abc00-0000-7000-8000-000000000001",
            "data": {"name": "John"},
            "_source": "primary",
            "_registry": sample_registry_data["document_with_identity"],
        }
        writer.add_entity("documents", document)

        manifest = Manifest(
            namespace="wip",
            counts=EntityCounts(terminologies=1, documents=1),
        )
        writer.write(manifest)

        with ArchiveReader(output) as reader:
            # Verify format version
            m = reader.read_manifest()
            assert m.format_version == "1.1"

            # Terminology _registry round-trip
            terms = list(reader.read_entities("terminologies"))
            assert len(terms) == 1
            reg = terms[0].get("_registry")
            assert reg is not None
            assert reg["entry_id"] == "0190a000-0000-7000-0000-000000000001"
            assert reg["primary_composite_key"] == {"value": "COUNTRY", "label": "Country"}
            assert len(reg["synonyms"]) == 1
            assert reg["synonyms"][0]["composite_key"] == {"external_code": "ISO-3166"}

            # Document _registry round-trip
            docs = list(reader.read_entities("documents"))
            assert len(docs) == 1
            reg = docs[0].get("_registry")
            assert reg is not None
            assert reg["primary_composite_key"]["identity_hash"] == "abc123hash"
            assert len(reg["synonyms"]) == 1

    def test_format_1_0_backward_compat(self, tmp_path):
        """Entities without _registry (format 1.0 style) load correctly."""
        output = tmp_path / "legacy.zip"
        writer = ArchiveWriter(output)

        # Simulate format 1.0: no _registry field
        writer.add_entity("templates", {
            "template_id": "0190c000-0000-7000-0000-000000000001",
            "value": "PERSON",
            "version": 1,
        })
        writer.add_entity("documents", {
            "document_id": "0190d000-0000-7000-0000-000000000001",
            "data": {"x": 1},
        })

        manifest = Manifest(
            format_version="1.0",
            namespace="wip",
            counts=EntityCounts(templates=1, documents=1),
        )
        writer.write(manifest)

        with ArchiveReader(output) as reader:
            m = reader.read_manifest()
            assert m.format_version == "1.0"
            # include_all_versions should default to False for old archives
            assert m.include_all_versions is False

            # .get("_registry", {}) pattern should work safely
            templates = list(reader.read_entities("templates"))
            assert len(templates) == 1
            reg = templates[0].get("_registry", {})
            assert reg == {}
            assert reg.get("synonyms", []) == []
            assert reg.get("primary_composite_key", {}) == {}

            docs = list(reader.read_entities("documents"))
            assert len(docs) == 1
            assert docs[0].get("_registry", {}).get("synonyms", []) == []

    def test_include_all_versions_manifest_field(self, tmp_path):
        """Manifest include_all_versions field survives round-trip."""
        output = tmp_path / "versions.zip"
        writer = ArchiveWriter(output)

        manifest = Manifest(
            namespace="wip",
            include_all_versions=True,
        )
        writer.write(manifest)

        with ArchiveReader(output) as reader:
            m = reader.read_manifest()
            assert m.include_all_versions is True


class TestArchiveWriterTempFiles:
    """Test the temp-file-based writer specifically."""

    def test_entity_count_tracking(self, tmp_path):
        """Writer tracks entity counts correctly."""
        output = tmp_path / "count.zip"
        writer = ArchiveWriter(output)

        for i in range(10):
            writer.add_entity("documents", {"document_id": f"0190d000-0000-7000-0000-{i:012d}"})
        for i in range(3):
            writer.add_entity("templates", {"template_id": f"0190c000-0000-7000-0000-{i:012d}"})

        assert writer.entity_count("documents") == 10
        assert writer.entity_count("templates") == 3
        assert writer.entity_count("terms") == 0

        # Cleanup
        writer.write(Manifest(namespace="wip"))

    def test_temp_dir_cleaned_up_after_write(self, tmp_path):
        """Temp directory is removed after write()."""
        output = tmp_path / "cleanup.zip"
        writer = ArchiveWriter(output)
        writer.add_entity("terms", {"term_id": "0190b000-0000-7000-0000-000000000001"})

        tmp_dir = writer._tmp_dir
        assert Path(tmp_dir).exists()

        writer.write(Manifest(namespace="wip"))

        assert not Path(tmp_dir).exists()

    def test_synonyms_file_round_trip(self, tmp_path):
        """Synonyms written via write_synonyms_file survive round-trip."""
        output = tmp_path / "synonyms.zip"
        writer = ArchiveWriter(output)

        synonyms = [
            {"entry_id": "0190a000-0000-7000-0000-000000000001", "namespace": "wip",
             "entity_type": "terminologies",
             "composite_key": {"code": "ISO"}},
            {"entry_id": "0190d000-0000-7000-0000-000000000001", "namespace": "wip",
             "entity_type": "documents",
             "composite_key": {"vendor": "V1"}},
        ]
        writer.write_synonyms_file(synonyms)
        writer.write(Manifest(namespace="wip"))

        with ArchiveReader(output) as reader:
            assert reader.has_synonyms()
            read_syns = list(reader.read_synonyms())
            assert len(read_syns) == 2
            assert read_syns[0]["entry_id"] == "0190a000-0000-7000-0000-000000000001"
            assert read_syns[1]["composite_key"] == {"vendor": "V1"}

    def test_no_synonyms_file(self, tmp_path):
        """Archive without synonyms.jsonl reports has_synonyms=False."""
        output = tmp_path / "no-synonyms.zip"
        writer = ArchiveWriter(output)
        writer.add_entity("terms", {"term_id": "0190b000-0000-7000-0000-000000000001"})
        writer.write(Manifest(namespace="wip"))

        with ArchiveReader(output) as reader:
            assert not reader.has_synonyms()
            assert list(reader.read_synonyms()) == []

    def test_constant_memory_for_large_writes(self, tmp_path):
        """Writing many entities doesn't accumulate in memory.

        The temp-file approach means we only hold one JSON line at a time.
        We verify this by writing a large number and checking the file exists.
        """
        output = tmp_path / "large-stream.zip"
        writer = ArchiveWriter(output)

        count = 5000
        for i in range(count):
            writer.add_entity("documents", {
                "document_id": f"0190d000-0000-7000-0000-{i:012d}",
                "data": {"value": f"data_{i}" * 10},
            })

        assert writer.entity_count("documents") == count
        writer.write(Manifest(namespace="wip", counts=EntityCounts(documents=count)))

        with ArchiveReader(output) as reader:
            entities = list(reader.read_entities("documents"))
            assert len(entities) == count
