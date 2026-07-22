"""v2.0 flat → v3 archive conversion (CASE-542).

Lives with the toolkit — convert_archive is an operator utility; the
archive format itself (reader/writer/manifest) is wip-archive's and is
tested there.
"""

import zipfile

import pytest
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.models import EntityCounts, Manifest, NamespaceEntry
from wip_toolkit.convert_archive import convert_archive


class TestConvertArchive:

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
