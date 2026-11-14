"""An empty-namespace archive restores, and its guard reads only what is stated.

The restore guard waived "the zip has no namespaces/ tree" when the manifest
listed its namespaces with every count at zero, but the model reads absent
counts as zero, so a manifest that left counts out passed as an empty backup:
the silent zero-entity restore the guard exists to stop. The waiver now needs
the counts stated, for the archive and for each namespace. The format gate
tested startswith("3") on a value the model defaults to 3.1 when absent; it
now needs a stated 3 or 3.N. And "can be restored" was pinned only at the
guard: these drive a manifest-only archive through restore and fresh.
"""

from __future__ import annotations

import json
import zipfile
from unittest.mock import AsyncMock

import pytest
from wip_archive import ArchiveReader
from wip_archive.models import Manifest

from document_store.services.backup_engine import DirectRestoreEngine, RestoreEngineError
from tests.test_case_1150 import _engine, _entry, _write
from tests.test_case_1161_run_restore import _registry_counters, mongo  # noqa: F401 — fixtures


def _raw_archive(tmp_path, manifest: dict):
    path = tmp_path / "raw.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
    return path


def _namespaces(path):
    with ArchiveReader(path) as reader:
        return _engine()._archive_namespaces(reader, reader.read_manifest())


class TestStatedOnly:
    def test_namespace_without_counts_is_refused(self, tmp_path):
        path = _raw_archive(tmp_path, {
            "format_version": "3.1", "counts": {},
            "namespaces": [{"prefix": "ns"}],
        })
        with pytest.raises(RestoreEngineError, match="no namespaces/ tree"):
            _namespaces(path)

    def test_archive_without_counts_is_refused(self, tmp_path):
        path = _raw_archive(tmp_path, {
            "format_version": "3.1",
            "namespaces": [{"prefix": "ns", "counts": {}}],
        })
        with pytest.raises(RestoreEngineError, match="no namespaces/ tree"):
            _namespaces(path)

    def test_stated_zero_counts_pass(self, tmp_path):
        path = _raw_archive(tmp_path, {
            "format_version": "3.1", "counts": {},
            "namespaces": [{"prefix": "ns", "counts": {}}],
        })
        assert _namespaces(path) == ["ns"]

    @pytest.mark.parametrize("version", [None, "30.0", "3-", "3.1.2", "4.0"])
    def test_a_format_not_stated_as_3_or_3n_is_refused(self, tmp_path, version):
        manifest = {"counts": {}, "namespaces": [{"prefix": "ns", "counts": {}}]}
        if version is not None:
            manifest["format_version"] = version
        path = _raw_archive(tmp_path, manifest)
        with pytest.raises(RestoreEngineError, match="restore engine reads the v3 layout"):
            _namespaces(path)

    @pytest.mark.parametrize("version", ["3", "3.0", "3.1", "3.12"])
    def test_3_and_3n_are_v3(self, version):
        assert Manifest(format_version=version).is_v3()
        assert not Manifest().is_v3()


def _empty_archive(tmp_path):
    return _write(tmp_path, Manifest(namespaces=[_entry("empty1274")], namespace="empty1274"))


@pytest.mark.asyncio
async def test_restore_of_an_empty_namespace_configures_it_and_completes(tmp_path, mongo):  # noqa: F811
    events: list = []
    engine = DirectRestoreEngine(mongo, None, events.append)
    engine._upsert_namespace = AsyncMock()
    await engine.run_restore(_empty_archive(tmp_path), "empty1274")
    engine._upsert_namespace.assert_awaited()
    namespace, config = engine._upsert_namespace.await_args.args[:2]
    assert namespace == "empty1274"
    assert config.namespace_id == "id-empty1274"
    assert events[-1].phase == "complete", events[-1]


@pytest.mark.asyncio
async def test_fresh_restore_of_an_empty_namespace_creates_the_target(tmp_path, mongo):  # noqa: F811
    events: list = []
    engine = DirectRestoreEngine(mongo, None, events.append)
    engine._upsert_namespace = AsyncMock()
    await engine.run_remap(_empty_archive(tmp_path), "empty1274copy")
    engine._upsert_namespace.assert_awaited()
    assert engine._upsert_namespace.await_args.args[0] == "empty1274copy"
    assert events[-1].phase == "complete", events[-1]
