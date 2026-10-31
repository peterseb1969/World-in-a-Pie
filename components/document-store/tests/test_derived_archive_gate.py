"""The restore door refuses an archive whose own verification failed.

An archive transform verifies its output and records the verdict in the
newest hop of the manifest's ``derived_from`` chain. A ``fail`` verdict means
the archive is known to be broken: every restore mode (id-preserving, fresh,
merge), dry runs included, refuses it before anything is written unless the
request sets ``allow_failed_verify``. Every other derived archive restores
with a warning, in all three modes. An accepted failure is recorded on the
namespace's restore provenance.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from wip_archive.archive import ArchiveWriter
from wip_archive.models import Manifest, NamespaceConfig, NamespaceEntry

from document_store.services.backup_engine import (
    _DB_REGISTRY,
    DirectRestoreEngine,
    ProgressEvent,
    RestoreEngineError,
)
from tests import test_case_942_restore_provenance as _p942
from tests.test_case_942_restore_provenance import (
    NAMESPACE,
    TERMINOLOGY,
    _http,
    _reader,
    _records,
)
from tests.test_merge_restore import REMAP_TARGET, _clear_remap_target, _run_remap

# The provenance tests' Mongo fixtures: a real database, cleaned around each
# test, with Beanie initialised for the remap path.
mongo = _p942.mongo
_beanie = _p942._beanie


def _hop(verdict: str | None, *, transform: str = "filter", **extra) -> dict:
    hop = {
        "transform": transform,
        "toolkit_version": "0.8.0",
        "at": "2026-10-31T12:00:00Z",
        "params": {"templates": ["MONSTER"]},
        "params_redacted": False,
        "input": {"sha256": "9f2c", "format_version": "3.1", "derived": False},
        **extra,
    }
    if verdict is not None:
        hop["verify"] = {
            "verdict": verdict,
            "findings": {"error": 3 if verdict == "fail" else 0, "warning": 1, "info": 0},
        }
    return hop


def _derived_archive(path: Path, derived_from) -> Path:
    """A real engine archive, then stamped with ``derived_from``.

    The writer serializes the typed Manifest, which has no ``derived_from``
    field, so it cannot write the marker itself; a transform adds the key to
    manifest.json the same way. Every other byte comes from the writer.
    """
    writer = ArchiveWriter(path, default_namespace=NAMESPACE)
    writer.add_entity("terminologies", dict(TERMINOLOGY), namespace=NAMESPACE)
    writer.write(Manifest(
        namespace=NAMESPACE,
        namespace_config=NamespaceConfig(prefix=NAMESPACE),
        namespaces=[NamespaceEntry(
            prefix=NAMESPACE, namespace_config=NamespaceConfig(prefix=NAMESPACE),
        )],
    ))
    stamped = path.with_suffix(".derived.zip")
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(stamped, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "manifest.json":
                manifest = json.loads(data)
                manifest["derived_from"] = derived_from
                data = json.dumps(manifest).encode()
            dst.writestr(item, data)
    return stamped


def _untouchable_mongo() -> MagicMock:
    """A Mongo client whose first use fails the test: the gate must refuse
    before the engine reads or writes anything."""
    client = MagicMock()
    client.__getitem__ = MagicMock(side_effect=AssertionError("mongo touched"))
    return client


async def _run(mode: str, engine: DirectRestoreEngine, archive: Path, **kw) -> None:
    if mode == "restore":
        await engine.run_restore(archive, NAMESPACE, **kw)
    elif mode == "fresh":
        await engine.run_remap(archive, "derived-gate-target", **kw)
    else:
        await engine.run_merge(archive, NAMESPACE, **kw)


MODES = ["restore", "fresh", "merge"]


class TestRefusal:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("dry_run", [False, True])
    async def test_a_failed_verdict_is_refused_before_anything_happens(
        self, tmp_path, mode, dry_run
    ):
        archive = _derived_archive(tmp_path / "a.zip", [_hop("fail")])
        events: list[ProgressEvent] = []
        engine = DirectRestoreEngine(_untouchable_mongo(), None, events.append)

        with pytest.raises(RestoreEngineError) as exc:
            await _run(mode, engine, archive, dry_run=dry_run)

        message = str(exc.value)
        assert "'filter' (toolkit 0.8.0)" in message
        assert "error=3" in message
        assert "allow_failed_verify" in message
        # Refused before the run started: no start event, nothing written.
        assert events == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", MODES)
    async def test_the_option_turns_the_refusal_into_a_warning(self, tmp_path, mode):
        archive = _derived_archive(tmp_path / "a.zip", [_hop("fail")])
        events: list[ProgressEvent] = []
        engine = DirectRestoreEngine(_untouchable_mongo(), None, events.append)

        # Past the gate the run reaches Mongo, which this stub forbids; the
        # warning before that is what this test is about.
        with pytest.raises(AssertionError, match="mongo touched"):
            await _run(mode, engine, archive, allow_failed_verify=True)

        assert events[0].phase == "warning"
        assert "verdict 'fail'" in events[0].message
        assert "allow_failed_verify=true" in events[0].message
        assert engine._accepted_failed_verify is True


class TestWarning:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("verdict", ["pass", "warn"])
    async def test_other_derived_archives_warn_in_every_mode(self, tmp_path, mode, verdict):
        archive = _derived_archive(tmp_path / "a.zip", [_hop(verdict)])
        events: list[ProgressEvent] = []
        engine = DirectRestoreEngine(_untouchable_mongo(), None, events.append)

        with pytest.raises(AssertionError, match="mongo touched"):
            await _run(mode, engine, archive)

        assert events[0].phase == "warning"
        assert "DERIVED" in events[0].message
        assert f"verify verdict '{verdict}'" in events[0].message
        assert engine._accepted_failed_verify is False


class TestRule:
    """Only the newest hop's verdict decides, and only 'fail' refuses."""

    @staticmethod
    def _gate(raw, *, allow=False):
        events: list[ProgressEvent] = []
        engine = DirectRestoreEngine(MagicMock(), None, events.append)
        engine._check_derived_archive(raw, allow_failed_verify=allow)
        return events

    def test_no_marker_is_a_full_backup(self):
        assert self._gate({}) == []
        assert self._gate({"derived_from": None}) == []
        assert self._gate({"derived_from": []}) == []

    def test_a_single_object_is_a_one_hop_chain(self):
        with pytest.raises(RestoreEngineError):
            self._gate({"derived_from": _hop("fail")})

    def test_an_older_failure_under_a_newer_pass_restores(self):
        events = self._gate({"derived_from": [_hop("fail"), _hop("pass", transform="repair")]})
        assert "'repair'" in events[0].message

    def test_a_newer_failure_over_an_older_pass_is_refused(self):
        with pytest.raises(RestoreEngineError, match="'latest-only'"):
            self._gate({"derived_from": [_hop("pass"), _hop("fail", transform="latest-only")]})

    def test_an_unknown_or_missing_verdict_warns(self):
        for hop in (_hop(None), _hop("needs-review")):
            [event] = self._gate({"derived_from": [hop]})
            assert event.phase == "warning"
            assert "DERIVED" in event.message

    def test_a_failed_hop_without_counts_is_still_refused(self):
        hop = _hop(None)
        hop["verify"] = {"verdict": "fail"}
        with pytest.raises(RestoreEngineError, match="no counts recorded"):
            self._gate({"derived_from": [hop]})


class TestProvenance:
    """An accepted failure is recorded on the restore provenance; a run that
    accepted nothing carries no such field."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("verdict,expected", [("fail", True), ("pass", None)])
    async def test_id_preserving_restore(self, mongo, tmp_path, verdict, expected):
        engine = DirectRestoreEngine(mongo, None, lambda _e: None)
        reader = _reader(
            {"terminologies": [TERMINOLOGY]}, raw={"derived_from": [_hop(verdict)]}
        )
        with patch(
            "document_store.services.backup_engine.ArchiveReader", return_value=reader
        ), patch("httpx.AsyncClient", return_value=_http()):
            await engine.run_restore(
                tmp_path / "x.zip", NAMESPACE,
                allow_missing_identity=True, allow_failed_verify=True,
            )

        [record] = await _records(mongo, NAMESPACE)
        assert record.get("accepted_failed_verify") is expected
        assert record["derived_from"][-1]["verify"]["verdict"] == verdict

    @pytest.mark.asyncio
    async def test_fresh_restore(self, mongo):
        await _clear_remap_target(mongo)
        await mongo[_DB_REGISTRY]["namespaces"].insert_one(
            {"prefix": REMAP_TARGET, "description": "", "isolation_mode": "open"}
        )
        try:
            reader = _reader(
                {"terminologies": [TERMINOLOGY]}, raw={"derived_from": [_hop("fail")]}
            )
            await _run_remap(mongo, reader, allow_failed_verify=True)

            [record] = await _records(mongo, REMAP_TARGET)
            assert record["mode"] == "remap"
            assert record["accepted_failed_verify"] is True
        finally:
            await _clear_remap_target(mongo)

    @pytest.mark.asyncio
    async def test_merge(self, mongo, tmp_path):
        await mongo[_DB_REGISTRY]["namespaces"].insert_one(
            {"prefix": NAMESPACE, "isolation_mode": "open"}
        )
        try:
            engine = DirectRestoreEngine(mongo, None, lambda _e: None)
            with patch(
                "document_store.services.backup_engine.ArchiveReader",
                return_value=_reader(
                    {"terminologies": [TERMINOLOGY]}, raw={"derived_from": [_hop("fail")]}
                ),
            ):
                await engine.run_merge(
                    tmp_path / "m.zip", NAMESPACE, add_missing=True, allow_failed_verify=True
                )
        finally:
            await mongo[_DB_REGISTRY]["namespaces"].delete_many({"prefix": NAMESPACE})

        [record] = await _records(mongo, NAMESPACE)
        assert record["mode"] == "merge"
        assert record["accepted_failed_verify"] is True
