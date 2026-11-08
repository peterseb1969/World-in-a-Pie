"""A backup of namespaces holding no entities can be restored.

The archive writer skips empty entity files, so such a backup is a zip with
only its manifest. The manifest still lists each namespace with its config and
all-zero counts. The restore's malformed-archive guard (no ``namespaces/``
tree) must accept that archive, and keep refusing a zip that lacks the tree
while its manifest claims entities or names no namespaces at all.
"""

from unittest.mock import MagicMock

import pytest
from wip_archive import ArchiveReader, ArchiveWriter
from wip_archive.models import EntityCounts, Manifest, NamespaceConfig, NamespaceEntry

from document_store.services.backup_engine import (
    DirectRestoreEngine,
    RestoreEngineError,
)


def _engine() -> DirectRestoreEngine:
    return DirectRestoreEngine(MagicMock(), None, lambda _: None)


def _write(tmp_path, manifest: Manifest, *, entities: dict[str, int] | None = None):
    """Assemble an archive through the real writer, as a backup does."""
    path = tmp_path / "archive.zip"
    writer = ArchiveWriter(path, tmp_dir=str(tmp_path))
    for ns, n in (entities or {}).items():
        for i in range(n):
            writer.add_entity("terminologies", {"terminology_id": f"t{i}"}, namespace=ns)
    writer.write(manifest)
    return path


def _entry(prefix: str, **counts: int) -> NamespaceEntry:
    return NamespaceEntry(
        prefix=prefix,
        namespace_config=NamespaceConfig(prefix=prefix, namespace_id=f"id-{prefix}"),
        counts=EntityCounts(**counts),
    )


def _namespaces(path):
    with ArchiveReader(path) as reader:
        return _engine()._archive_namespaces(reader, reader.read_manifest())


class TestEmptyNamespaceArchive:
    def test_writer_leaves_no_subtree_for_an_empty_namespace(self, tmp_path):
        path = _write(tmp_path, Manifest(namespaces=[_entry("empty")], namespace="empty"))
        with ArchiveReader(path) as reader:
            assert reader.list_namespaces() == []

    def test_single_empty_namespace_is_restorable(self, tmp_path):
        path = _write(tmp_path, Manifest(namespaces=[_entry("empty")], namespace="empty"))
        assert _namespaces(path) == ["empty"]

    def test_several_empty_namespaces_are_restorable(self, tmp_path):
        path = _write(tmp_path, Manifest(namespaces=[_entry("a"), _entry("b")]))
        assert _namespaces(path) == ["a", "b"]

    def test_mixed_archive_still_lists_the_empty_namespace(self, tmp_path):
        manifest = Manifest(
            namespaces=[_entry("full", terminologies=1), _entry("empty")],
            counts=EntityCounts(terminologies=1),
        )
        path = _write(tmp_path, manifest, entities={"full": 1})
        assert _namespaces(path) == ["full", "empty"]


class TestTruncatedArchiveStillRefused:
    def test_namespace_counts_entities_the_zip_lacks(self, tmp_path):
        manifest = Manifest(
            namespaces=[_entry("ns", terms=3)], counts=EntityCounts(terms=3)
        )
        path = _write(tmp_path, manifest)
        with pytest.raises(RestoreEngineError, match="no namespaces/ tree"):
            _namespaces(path)

    def test_aggregate_counts_entities_the_zip_lacks(self, tmp_path):
        manifest = Manifest(namespaces=[_entry("ns")], counts=EntityCounts(documents=2))
        path = _write(tmp_path, manifest)
        with pytest.raises(RestoreEngineError, match="no namespaces/ tree"):
            _namespaces(path)

    def test_manifest_naming_no_namespace_list(self, tmp_path):
        # Zero counts alone prove nothing: a legacy-shaped manifest has no
        # namespaces list and no counts, which read as zero.
        path = _write(tmp_path, Manifest(namespace="wip"))
        with pytest.raises(RestoreEngineError, match="no namespaces/ tree"):
            _namespaces(path)

    @pytest.mark.asyncio
    async def test_run_restore_refuses_truncated(self, tmp_path):
        manifest = Manifest(
            namespaces=[_entry("ns", terms=3)], counts=EntityCounts(terms=3)
        )
        path = _write(tmp_path, manifest)
        with pytest.raises(RestoreEngineError, match="no namespaces/ tree"):
            await _engine().run_restore(path, "ns")
