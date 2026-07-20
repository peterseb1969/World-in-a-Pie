"""Tests for archive read/write round-trip."""

import zipfile
from pathlib import Path

import pytest
from wip_toolkit.archive import ENTITY_FILES, ArchiveReader, ArchiveWriter
from wip_toolkit.convert_archive import convert_archive
from wip_toolkit.models import EntityCounts, Manifest, NamespaceEntry


class TestArchiveRoundTrip:
    """Test that entities survive a write/read round-trip."""

    def test_empty_archive(self, tmp_path):
        """Empty archive has valid manifest and zero entities."""
        output = tmp_path / "empty.zip"
        writer = ArchiveWriter(output, default_namespace="wip")
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
        writer = ArchiveWriter(output, default_namespace="wip")

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
        writer = ArchiveWriter(output, default_namespace="wip")

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
        writer = ArchiveWriter(output, default_namespace="wip")

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
        writer = ArchiveWriter(output, default_namespace="wip")

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

        writer = ArchiveWriter(output, tmp_dir=scratch_root, default_namespace="wip")

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
        writer = ArchiveWriter(output, default_namespace="wip")

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
        writer = ArchiveWriter(output, default_namespace="wip")

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
        writer = ArchiveWriter(output, default_namespace="wip")

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
        writer = ArchiveWriter(output, default_namespace="wip")
        for i in range(7):
            writer.add_entity("terms", {"term_id": f"0190b000-0000-7000-0000-{i:012d}"})

        manifest = Manifest(namespace="wip", counts=EntityCounts(terms=7))
        writer.write(manifest)

        with ArchiveReader(output) as reader:
            assert reader.entity_count("terms") == 7
            assert reader.entity_count("documents") == 0

    def test_archive_sizes(self, tmp_path):
        output = tmp_path / "sizes.zip"
        writer = ArchiveWriter(output, default_namespace="wip")
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
        writer = ArchiveWriter(output, default_namespace="wip")

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
            assert m.format_version == "3.0"

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
        writer = ArchiveWriter(output, default_namespace="wip")

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
        writer = ArchiveWriter(output, default_namespace="wip")

        manifest = Manifest(
            namespace="wip",
            include_all_versions=True,
        )
        writer.write(manifest)

        with ArchiveReader(output) as reader:
            m = reader.read_manifest()
            assert m.include_all_versions is True


class TestMultiNamespaceArchive:
    """v3 multi-namespace archive layout (CASE-542)."""

    def test_two_namespaces_round_trip(self, tmp_path):
        output = tmp_path / "multi-ns.zip"
        writer = ArchiveWriter(output)

        writer.add_entity("terms", {"term_id": "T-A1", "value": "a1"}, namespace="alpha")
        writer.add_entity("terms", {"term_id": "T-A2", "value": "a2"}, namespace="alpha")
        writer.add_entity("documents", {"document_id": "D-B1"}, namespace="beta")

        assert writer.namespaces() == ["alpha", "beta"]
        assert writer.entity_count("terms", namespace="alpha") == 2
        assert writer.entity_count("documents", namespace="beta") == 1

        writer.write(Manifest(
            namespaces=[
                NamespaceEntry(prefix="alpha", counts=EntityCounts(terms=2)),
                NamespaceEntry(prefix="beta", counts=EntityCounts(documents=1)),
            ],
            counts=EntityCounts(terms=2, documents=1),
        ))

        with ArchiveReader(output) as reader:
            assert reader.list_namespaces() == ["alpha", "beta"]
            assert reader.read_manifest().namespace_prefixes() == ["alpha", "beta"]
            alpha_terms = list(reader.read_entities("terms", namespace="alpha"))
            assert {t["term_id"] for t in alpha_terms} == {"T-A1", "T-A2"}
            assert list(reader.read_entities("terms", namespace="beta")) == []
            assert reader.entity_count("documents", namespace="beta") == 1

    def test_layout_is_per_namespace(self, tmp_path):
        output = tmp_path / "layout.zip"
        writer = ArchiveWriter(output)
        writer.add_entity("terms", {"term_id": "X"}, namespace="ns1")
        writer.write(Manifest(namespaces=[NamespaceEntry(prefix="ns1")]))

        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert "namespaces/ns1/terms.jsonl" in names
        assert "terms.jsonl" not in names  # not at root anymore

    def test_read_entities_ambiguous_without_namespace(self, tmp_path):
        output = tmp_path / "ambig.zip"
        writer = ArchiveWriter(output)
        writer.add_entity("terms", {"term_id": "A"}, namespace="alpha")
        writer.add_entity("terms", {"term_id": "B"}, namespace="beta")
        writer.write(Manifest(namespaces=[
            NamespaceEntry(prefix="alpha"), NamespaceEntry(prefix="beta"),
        ]))
        with ArchiveReader(output) as reader, pytest.raises(ValueError, match="2 namespaces"):
            list(reader.read_entities("terms"))


class TestConvertArchive:
    """v2.0 flat → v3 conversion (CASE-542)."""

    def _write_legacy_v2(self, path):
        """Hand-build an old flat v2.0 archive (entities at root)."""
        manifest = Manifest(
            format_version="2.0", namespace="legacy",
            counts=EntityCounts(terms=2, documents=1),
        )
        # Clear the v3 namespaces list to mimic a true v2.0 manifest.
        manifest.namespaces = []
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", manifest.model_dump_json())
            zf.writestr("terms.jsonl", '{"term_id":"T1"}\n{"term_id":"T2"}\n')
            zf.writestr("documents.jsonl", '{"document_id":"D1"}\n')
            zf.writestr("blobs/FILE-1", b"blobdata")

    def test_convert_v2_to_v3(self, tmp_path):
        old = tmp_path / "old.zip"
        new = tmp_path / "new.zip"
        self._write_legacy_v2(old)

        convert_archive(old, new)

        with ArchiveReader(new) as reader:
            m = reader.read_manifest()
            assert m.format_version == "3.0"
            assert reader.list_namespaces() == ["legacy"]
            terms = list(reader.read_entities("terms", namespace="legacy"))
            assert {t["term_id"] for t in terms} == {"T1", "T2"}
            docs = list(reader.read_entities("documents", namespace="legacy"))
            assert docs[0]["document_id"] == "D1"
            assert reader.read_blob("FILE-1") == b"blobdata"  # blobs stay flat

    def test_convert_rejects_already_v3(self, tmp_path):
        v3 = tmp_path / "v3.zip"
        writer = ArchiveWriter(v3, default_namespace="x")
        writer.add_entity("terms", {"term_id": "T"})
        writer.write(Manifest(namespaces=[NamespaceEntry(prefix="x")]))
        with pytest.raises(ValueError, match="already v3"):
            convert_archive(v3, tmp_path / "out.zip")


class TestArchiveWriterTempFiles:
    """Test the temp-file-based writer specifically."""

    def test_entity_count_tracking(self, tmp_path):
        """Writer tracks entity counts correctly."""
        output = tmp_path / "count.zip"
        writer = ArchiveWriter(output, default_namespace="wip")

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
        writer = ArchiveWriter(output, default_namespace="wip")
        writer.add_entity("terms", {"term_id": "0190b000-0000-7000-0000-000000000001"})

        tmp_dir = writer._tmp_dir
        assert Path(tmp_dir).exists()

        writer.write(Manifest(namespace="wip"))

        assert not Path(tmp_dir).exists()

    def test_synonyms_file_round_trip(self, tmp_path):
        """Synonyms written via write_synonyms_file survive round-trip."""
        output = tmp_path / "synonyms.zip"
        writer = ArchiveWriter(output, default_namespace="wip")

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
        writer = ArchiveWriter(output, default_namespace="wip")
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
        writer = ArchiveWriter(output, default_namespace="wip")

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


class TestStreamingReads:
    """The reader must not materialise an entity file to yield one row.

    The writer side has always been O(1) in memory (it spools to temp files);
    the reader used to decode a whole member into one Python string, which for
    a large namespace's documents is hundreds of megabytes for something
    consumed a row at a time — and twice over, since counting re-read it.
    """

    @staticmethod
    def _archive(tmp_path, rows=2000):
        writer = ArchiveWriter(tmp_path / "stream.zip")
        for index in range(rows):
            writer.add_entity(
                "documents",
                {"document_id": f"D{index}", "payload": "x" * 100},
                namespace="kb",
            )
        writer.write(Manifest(
            format_version="3.0",
            namespace="kb",
            counts=EntityCounts(documents=rows),
        ))
        return tmp_path / "stream.zip"

    def test_reading_is_lazy(self, tmp_path):
        # The first row must arrive without the rest having been read.
        with ArchiveReader(self._archive(tmp_path)) as reader:
            rows = reader.read_entities("documents", namespace="kb")
            assert next(rows)["document_id"] == "D0"
            assert next(rows)["document_id"] == "D1"

    def test_every_row_is_yielded(self, tmp_path):
        with ArchiveReader(self._archive(tmp_path)) as reader:
            rows = list(reader.read_entities("documents", namespace="kb"))
        assert len(rows) == 2000
        assert rows[-1]["document_id"] == "D1999"

    def test_counting_matches_reading(self, tmp_path):
        with ArchiveReader(self._archive(tmp_path)) as reader:
            counted = reader.entity_count("documents", namespace="kb")
            read = sum(1 for _ in reader.read_entities("documents", namespace="kb"))
        assert counted == read == 2000

    def test_a_missing_entity_file_yields_nothing(self, tmp_path):
        with ArchiveReader(self._archive(tmp_path)) as reader:
            assert list(reader.read_entities("terminologies", namespace="kb")) == []
            assert reader.entity_count("terminologies", namespace="kb") == 0

    def test_reads_can_be_reopened(self, tmp_path):
        # Streaming opens a fresh member handle per call; a second pass must
        # not find an exhausted one.
        with ArchiveReader(self._archive(tmp_path, rows=10)) as reader:
            first = list(reader.read_entities("documents", namespace="kb"))
            second = list(reader.read_entities("documents", namespace="kb"))
        assert first == second and len(first) == 10
